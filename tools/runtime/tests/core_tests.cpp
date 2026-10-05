// Tests for the platform-independent runtime core (bridge, redirect, selection, runtime).
#include <cstring>
#include <iostream>
#include <map>
#include <stdexcept>

#include "../src/core/bridge.hpp"
#include "../src/core/redirect.hpp"
#include "../src/core/runtime.hpp"
#include "../src/core/selection.hpp"

using namespace ua;

static int failures = 0;
#define CHECK(cond)                                                                       \
    do {                                                                                  \
        if (!(cond)) {                                                                    \
            std::cerr << __FILE__ << ":" << __LINE__ << ": CHECK failed: " #cond "\n";    \
            ++failures;                                                                   \
        }                                                                                 \
    } while (0)

// Fake process memory: a few byte vectors at made-up addresses.
class FakeMemory : public Memory {
public:
    std::map<std::uintptr_t, std::vector<std::uint8_t>> blocks;

    std::vector<Region> regions() override {
        std::vector<Region> r;
        for (auto& [base, b] : blocks) r.push_back({base, b.size()});
        return r;
    }
    std::uint8_t* at(std::uintptr_t addr, std::size_t n) {
        for (auto& [base, b] : blocks)
            if (addr >= base && addr + n <= base + b.size()) return b.data() + (addr - base);
        return nullptr;
    }
    bool read(std::uintptr_t addr, void* out, std::size_t n) override {
        auto* p = at(addr, n);
        if (!p) return false;
        std::memcpy(out, p, n);
        return true;
    }
    bool write(std::uintptr_t addr, const void* in, std::size_t n) override {
        auto* p = at(addr, n);
        if (!p) return false;
        std::memcpy(p, in, n);
        return true;
    }
    void put(std::uintptr_t addr, std::uint32_t v) { write(addr, &v, 4); }
    std::uint32_t get(std::uintptr_t addr) {
        std::uint32_t v = 0;
        read(addr, &v, 4);
        return v;
    }
};

constexpr std::uint32_t MAGIC = 0x55410003, SEED = 0x41524D59;
constexpr int SLOTS = 7;

// Lay out a globals table like the script declares it: magic, echo, slots..., tick, seed.
// `stride` models the engine's per-global record size (value at offset 0).
static std::uintptr_t lay_globals(FakeMemory& m, std::uintptr_t base, std::size_t stride, bool started) {
    std::uintptr_t a = base + 0x40;
    m.put(a, MAGIC);
    m.put(a + stride, started ? SEED : 0);
    for (int k = 0; k < SLOTS; ++k) m.put(a + stride * (2 + k), 0);
    m.put(a + stride * (2 + SLOTS), 0);           // tick
    m.put(a + stride * (3 + SLOTS), SEED);        // ua_seed
    return a;
}

static void test_bridge_finds_live_globals_only() {
    FakeMemory m;
    m.blocks[0x10000] = std::vector<std::uint8_t>(4096);
    m.blocks[0x90000] = std::vector<std::uint8_t>(4096);
    // Decoy: the map's own script data has the literals, but MAGIC sits next to 0 and SEED is far away.
    std::uintptr_t decoy = 0x10000 + 0x100;
    m.put(decoy, MAGIC);
    m.put(decoy + 8, 0);
    m.put(decoy + 400, SEED);
    std::uintptr_t g = lay_globals(m, 0x90000, 8, /*started=*/true);

    Bridge b(m);
    BridgeTarget t{MAGIC, SEED, SLOTS};
    b.scan(t);
    CHECK(b.candidates().size() == 1);
    CHECK(b.candidates()[0].magic_addr == g);
    CHECK(b.candidates()[0].stride == 8);
    CHECK(!b.connected());

    std::vector<std::int32_t> values{3, 1, 0, 0, 2, 0, 1};
    CHECK(b.update(t, values) == 0);           // not live yet: nothing written
    CHECK(m.get(g + 8 * 2) == 0);
    m.put(g + 8 * (2 + SLOTS), 1);             // the script's loop flips ua_tick
    CHECK(b.update(t, values) == 1);
    CHECK(b.connected());
    for (int k = 0; k < SLOTS; ++k) CHECK(m.get(g + 8 * (2 + k)) == static_cast<std::uint32_t>(values[k]));
    CHECK(m.get(decoy + 8) == 0);              // decoy untouched
}

static void test_bridge_ignores_unstarted_script_and_drops_vanished_memory() {
    FakeMemory m;
    m.blocks[0x20000] = std::vector<std::uint8_t>(4096);
    lay_globals(m, 0x20000, 4, /*started=*/false);
    Bridge b(m);
    BridgeTarget t{MAGIC, SEED, SLOTS};
    b.scan(t);
    CHECK(b.candidates().empty());             // echo not set yet

    std::uintptr_t g = lay_globals(m, 0x20000, 4, /*started=*/true);
    b.scan(t);
    CHECK(b.candidates().size() == 1 && b.candidates()[0].stride == 4);
    m.put(g + 4 * (2 + SLOTS), 1);
    b.update(t, {1, 1, 1, 1, 1, 1, 1});
    CHECK(b.connected());
    m.blocks.erase(0x20000);                   // mission unloaded
    CHECK(b.update(t, {1, 1, 1, 1, 1, 1, 1}) == 0);
    CHECK(b.candidates().empty() && !b.connected());
}

static void test_bridge_rejects_wrong_game_and_garbage() {
    FakeMemory m;
    m.blocks[0x30000] = std::vector<std::uint8_t>(4096);
    std::uintptr_t g = lay_globals(m, 0x30000, 8, true);
    Bridge b(m);
    b.scan({MAGIC + 1, SEED, SLOTS});          // another game's magic
    CHECK(b.candidates().empty());
    m.put(g + 8 * 2, 0x7FFFFFFF);              // implausible slot value
    b.scan({MAGIC, SEED, SLOTS});
    CHECK(b.candidates().empty());
}

static void test_bridge_rejects_nearby_seed_global() {
    // Tightly packed globals before the script starts: ua_seed is within reach of ua_magic, but
    // ua_echo is still 0. That must not be mistaken for a table with a wider stride.
    FakeMemory m;
    m.blocks[0x40000] = std::vector<std::uint8_t>(4096);
    lay_globals(m, 0x40000, 4, /*started=*/false);
    Bridge b(m);
    b.scan({MAGIC, SEED, SLOTS});
    CHECK(b.candidates().empty());
}

// Memory that also exposes the bridge's own scan buffer, as a real process would.
class SelfVisibleMemory : public FakeMemory {
public:
    std::vector<Region> extra;
    std::vector<Region> regions() override {
        auto r = FakeMemory::regions();
        r.insert(r.end(), extra.begin(), extra.end());
        return r;
    }
    bool read(std::uintptr_t addr, void* out, std::size_t n) override {
        for (auto& e : extra)
            if (addr >= e.base && addr + n <= e.base + e.size) {
                std::memmove(out, reinterpret_cast<void*>(addr), n);
                return true;
            }
        return FakeMemory::read(addr, out, n);
    }
};

static void test_bridge_ignores_its_own_scan_buffer() {
    SelfVisibleMemory m;
    m.blocks[0x60000] = std::vector<std::uint8_t>(4096);
    std::uintptr_t g = lay_globals(m, 0x60000, 8, true);
    Bridge b(m);
    b.scan({MAGIC, SEED, SLOTS});              // leaves a copy of the globals in the scan buffer
    CHECK(b.candidates().size() == 1);
    m.extra.push_back(b.scan_buffer());        // in a real process the buffer is scannable heap memory
    b.scan({MAGIC, SEED, SLOTS});
    CHECK(b.candidates().size() == 1);
    CHECK(!b.candidates().empty() && b.candidates()[0].magic_addr == g);
}

static void test_bridge_pair_across_chunk_boundary() {
    FakeMemory m;
    std::size_t size = (1 << 20) + 4096;
    m.blocks[0x1000000] = std::vector<std::uint8_t>(size);
    std::uintptr_t a = 0x1000000 + (1 << 20) - 8;  // magic in chunk 1, echo in chunk 2
    m.put(a, MAGIC);
    m.put(a + 16, SEED);
    for (int k = 0; k < SLOTS + 1; ++k) m.put(a + 16 * (2 + k), 0);
    m.put(a + 16 * (3 + SLOTS), SEED);
    Bridge b(m);
    b.scan({MAGIC, SEED, SLOTS});
    CHECK(b.candidates().size() == 1 && b.candidates()[0].stride == 16);
}

static const char* kManifest = R"({
  "format": "unified-armory-pack/1", "seed": 1095912793,
  "slot_order": ["helmet", "chest"], "slots": {"helmet": "Helmet", "chest": "Chest"},
  "games": {
    "reach": {"name": "Halo: Reach", "index": 5, "magic": 1430323205, "maps": ["haloreach/maps/m10.map"],
      "slots": {"helmet": [{"index": 0, "uid": "own", "name": "own", "from": "reach"},
                           {"index": 1, "uid": "reach/helmet_gungnir", "name": "Gungnir", "from": "reach"}],
                "chest": [{"index": 0, "uid": "own", "name": "own", "from": "reach"}]}},
    "halo3": {"name": "Halo 3", "index": 3, "magic": 1430323203, "maps": ["halo3/maps/010_jungle.map"],
      "slots": {"helmet": [{"index": 0, "uid": "own", "name": "own", "from": "halo3"},
                           {"index": 1, "uid": "halo3/helmet_eod", "name": "EOD", "from": "halo3"},
                           {"index": 2, "uid": "reach/helmet_gungnir", "name": "Gungnir", "from": "reach"}],
                "chest": [{"index": 0, "uid": "own", "name": "own", "from": "halo3"},
                          {"index": 1, "uid": "halo3/chest_eod", "name": "EOD", "from": "halo3"}]}}
  }
})";

static void test_manifest_and_selection() {
    Manifest m = parse_manifest(kManifest);
    CHECK(m.games.size() == 2 && m.games[0].id == "halo3");  // ordered by magic (game order)
    CHECK(m.game_by_magic(1430323205)->id == "reach");
    auto opts = m.options("helmet");
    CHECK(opts.size() == 2 && opts[0].uid == "halo3/helmet_eod" && opts[1].uid == "reach/helmet_gungnir");

    Selection s;
    s.armor["helmet"] = "reach/helmet_gungnir";
    s.armor["chest"] = "halo3/chest_eod";
    CHECK((resolve(m, s, "halo3") == std::vector<std::int32_t>{2, 1}));
    CHECK((resolve(m, s, "reach") == std::vector<std::int32_t>{1, 0}));  // EOD chest not in Reach's pack -> own
    s.overrides["reach"]["helmet"] = "own";
    CHECK((resolve(m, s, "reach") == std::vector<std::int32_t>{0, 0}));
    s.armor["helmet"] = "default";
    CHECK((resolve(m, s, "halo3") == std::vector<std::int32_t>{0, 1}));

    Selection back = parse_selection(dump_selection(s));
    CHECK(back.armor == s.armor && back.overrides == s.overrides);
    CHECK(parse_selection("not json").armor.empty());

    bool threw = false;
    try { parse_manifest(R"({"format": "something-else"})"); } catch (const std::exception&) { threw = true; }
    CHECK(threw);
}

static void test_redirect() {
    Redirector r("C:\\Steam\\steamapps\\common\\Halo The Master Chief Collection",
                 "C:\\Steam\\steamapps\\common\\Halo The Master Chief Collection\\mcc\\binaries\\win64\\UnifiedArmory\\maps",
                 {{"halo3/maps/010_jungle.map", "halo3"}});
    auto hit = r.match("\\\\?\\C:\\STEAM\\steamapps\\common\\Halo The Master Chief Collection\\halo3\\maps\\010_JUNGLE.map");
    CHECK(hit.has_value());
    CHECK(hit && hit->game == "halo3");
    CHECK(hit && hit->path == "C:\\Steam\\steamapps\\common\\Halo The Master Chief Collection\\mcc\\binaries\\win64\\UnifiedArmory\\maps\\halo3\\maps\\010_jungle.map");
    CHECK(!r.match("C:\\Steam\\steamapps\\common\\Halo The Master Chief Collection\\halo3\\maps\\shared.map"));
    CHECK(!r.match("D:\\Other\\halo3\\maps\\010_jungle.map"));
    CHECK(normalize_path("C:\\\\A//b") == "c:/a/b");
}

static void test_runtime_end_to_end() {
    FakeMemory mem;
    mem.blocks[0x50000] = std::vector<std::uint8_t>(4096);
    Manifest m = parse_manifest(kManifest);
    Selection s;
    s.armor["helmet"] = "reach/helmet_gungnir";
    Runtime rt(mem, m, s);
    auto t0 = Runtime::Clock::now();
    rt.tick(t0);                                  // no map yet: nothing happens
    CHECK(rt.status().game.empty());

    rt.on_map_opened("halo3", t0);
    // Mission script globals for Halo 3: magic, echo, helmet, chest, tick, seed (stride 8)
    std::uintptr_t g = 0x50000 + 0x80;
    mem.put(g, 1430323203);
    mem.put(g + 8, 1095912793);
    mem.put(g + 16, 0);
    mem.put(g + 24, 0);
    mem.put(g + 32, 0);                           // tick
    mem.put(g + 40, 1095912793);                  // ua_seed
    rt.tick(t0);                                  // scan: candidate found, not live yet
    CHECK(!rt.status().connected);
    mem.put(g + 32, 1);                           // script loop runs
    rt.tick(t0 + std::chrono::milliseconds(500));
    CHECK(rt.status().connected);
    CHECK(mem.get(g + 16) == 2);                  // Gungnir is option 2 in Halo 3's pack

    Selection s2 = rt.selection();
    s2.armor["helmet"] = "halo3/helmet_eod";      // changed in the overlay
    rt.set_selection(s2);
    rt.tick(t0 + std::chrono::milliseconds(750));
    CHECK(mem.get(g + 16) == 1);
}

int main() {
    test_bridge_finds_live_globals_only();
    test_bridge_ignores_unstarted_script_and_drops_vanished_memory();
    test_bridge_rejects_wrong_game_and_garbage();
    test_bridge_rejects_nearby_seed_global();
    test_bridge_pair_across_chunk_boundary();
    test_bridge_ignores_its_own_scan_buffer();
    test_manifest_and_selection();
    test_redirect();
    test_runtime_end_to_end();
    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "all runtime core tests passed\n";
    return 0;
}
