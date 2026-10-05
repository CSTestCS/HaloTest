#include "bridge.hpp"

#include <algorithm>
#include <cstring>

namespace ua {

namespace {
bool read_u32(Memory& mem, std::uintptr_t addr, std::uint32_t& out) { return mem.read(addr, &out, sizeof out); }
}  // namespace

void Bridge::scan(const BridgeTarget& t) {
    candidates_.clear();
    auto& buf = buf_;
    const auto own_lo = reinterpret_cast<std::uintptr_t>(buf_.data());
    const auto own_hi = own_lo + buf_.size();
    for (const Region& r : mem_.regions()) {
        // Read in chunks that overlap by kMaxStride so a pair straddling a boundary is still seen.
        for (std::size_t off = 0; off < r.size; off += kChunk) {
            std::size_t n = std::min(kChunk + kMaxStride, r.size - off);
            if (!mem_.read(r.base + off, buf.data(), n)) continue;
            for (std::size_t i = 0; i + 4 <= std::min(n, kChunk); i += 4) {
                std::uint32_t v;
                std::memcpy(&v, buf.data() + i, 4);
                if (v != t.magic) continue;
                for (std::size_t s = 4; s <= kMaxStride && i + s + 4 <= n; s += 4) {
                    std::uint32_t w;
                    std::memcpy(&w, buf.data() + i + s, 4);
                    if (w != t.seed) continue;
                    Candidate c{r.base + off + i, s};
                    bool inside_own_buffer = c.magic_addr >= own_lo && c.magic_addr < own_hi;
                    if (!inside_own_buffer && still_valid(t, c)) candidates_.push_back(c);
                    break;
                }
            }
        }
    }
}

bool Bridge::still_valid(const BridgeTarget& t, Candidate& c) {
    std::uint32_t magic, echo;
    if (!read_u32(mem_, c.magic_addr, magic) || !read_u32(mem_, c.magic_addr + c.stride, echo)) return false;
    if (magic != t.magic || echo != t.seed) return false;
    for (int k = 0; k < t.slot_count; ++k) {
        std::uint32_t v;
        if (!read_u32(mem_, c.slot_addr(k), v)) return false;
        if (static_cast<std::int32_t>(v) < 0 || static_cast<std::int32_t>(v) >= kMaxSlotValue) return false;
    }
    std::uint32_t tick, seed;
    if (!read_u32(mem_, c.tick_addr(t.slot_count), tick) || tick > 1) return false;
    // ua_seed is declared right after ua_tick: it must sit exactly there at this stride. This rejects
    // a false stride where the real ua_seed happens to lie within reach of ua_magic.
    if (!read_u32(mem_, c.tick_addr(t.slot_count) + c.stride, seed) || seed != t.seed) return false;
    if (c.last_tick >= 0 && static_cast<std::int32_t>(tick) != c.last_tick) c.live = true;
    c.last_tick = static_cast<std::int32_t>(tick);
    return true;
}

int Bridge::update(const BridgeTarget& t, const std::vector<std::int32_t>& values) {
    std::vector<Candidate> kept;
    for (Candidate& c : candidates_)
        if (still_valid(t, c)) kept.push_back(c);
    candidates_.swap(kept);
    int written = 0;
    for (Candidate& c : candidates_) {
        if (!c.live) continue;
        for (int k = 0; k < t.slot_count && k < static_cast<int>(values.size()); ++k) {
            std::int32_t v = std::clamp<std::int32_t>(values[k], 0, kMaxSlotValue - 1);
            std::int32_t cur;
            if (mem_.read(c.slot_addr(k), &cur, 4) && cur != v) mem_.write(c.slot_addr(k), &v, 4);
        }
        ++written;
    }
    return written;
}

bool Bridge::connected() const {
    return std::any_of(candidates_.begin(), candidates_.end(), [](const Candidate& c) { return c.live; });
}

}  // namespace ua
