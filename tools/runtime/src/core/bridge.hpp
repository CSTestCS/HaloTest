// The bridge finds the Unified Armory mission script's globals in a running campaign
// and writes the player's selection into them. It needs no per-game addresses:
//
//   The script declares, in order:  ua_magic, ua_echo, <one long per slot>, ua_tick, ..., ua_seed
//   ua_magic = MAGIC (per game), ua_echo starts at 0 and the script copies ua_seed (SEED) into it
//   at mission start; ua_tick flips between 0 and 1 every loop.
//
//   So MAGIC directly followed (within a few words) by SEED only exists in the live globals
//   table. The distance between them is the spacing of globals, and ua_seed must then sit
//   exactly where the declaration order puts it. A candidate is trusted only
//   after ua_tick is seen to change, i.e. a running script owns that memory.
#pragma once
#include <chrono>
#include <cstdint>
#include <optional>
#include <vector>

#include "memory.hpp"

namespace ua {

struct BridgeTarget {
    std::uint32_t magic = 0;   // MAGIC_BASE + game index
    std::uint32_t seed = 0;    // shared SEED
    int slot_count = 0;
};

struct Candidate {
    std::uintptr_t magic_addr = 0;
    std::size_t stride = 0;
    std::int32_t last_tick = -1;
    bool live = false;

    std::uintptr_t slot_addr(int slot) const { return magic_addr + stride * (2 + slot); }
    std::uintptr_t tick_addr(int slot_count) const { return magic_addr + stride * (2 + slot_count); }
};

class Bridge {
public:
    explicit Bridge(Memory& mem) : mem_(mem) { buf_.resize(kChunk + kMaxStride); }

    // Look for the script's globals. Clears previous candidates.
    void scan(const BridgeTarget& target);

    // Re-check candidates (drop the ones whose memory changed or vanished), promote the
    // ones whose tick moved to live, and write `values` (one per slot) into live ones.
    // Returns the number of live candidates written.
    int update(const BridgeTarget& target, const std::vector<std::int32_t>& values);

    bool connected() const;
    const std::vector<Candidate>& candidates() const { return candidates_; }
    Region scan_buffer() const { return {reinterpret_cast<std::uintptr_t>(buf_.data()), buf_.size()}; }

    static constexpr std::size_t kMaxStride = 64;
    static constexpr std::size_t kChunk = 1 << 20;
    static constexpr std::int32_t kMaxSlotValue = 4096;

private:
    bool still_valid(const BridgeTarget& target, Candidate& c);
    Memory& mem_;
    std::vector<Candidate> candidates_;
    // Scan buffer, allocated once and never resized: it lives in scanned memory itself, so
    // anything "found" inside it is a copy and is discarded.
    std::vector<std::uint8_t> buf_;
};

}  // namespace ua
