// Ties the pieces together: when a campaign map is opened (seen by the file redirect),
// scan for that mission's script globals, then keep writing the current selection into them.
#pragma once
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>

#include "bridge.hpp"
#include "selection.hpp"

namespace ua {

class Runtime {
public:
    using Clock = std::chrono::steady_clock;

    Runtime(Memory& mem, Manifest manifest, Selection selection);

    // A pack map for `game` was opened: the mission's globals will appear shortly.
    void on_map_opened(const std::string& game, Clock::time_point now);
    // Call a few times a second from a background thread.
    void tick(Clock::time_point now);

    Selection selection() const;
    void set_selection(Selection s);
    const Manifest& manifest() const { return manifest_; }

    struct Status {
        std::string game;  // empty when no pack map has been loaded
        bool connected = false;
        bool searching = false;
    };
    Status status() const;

    static constexpr std::chrono::seconds kSearchWindow{180};
    static constexpr std::chrono::milliseconds kScanInterval{2000};

private:
    Memory& mem_;
    Manifest manifest_;
    mutable std::mutex mu_;
    Selection selection_;
    std::string game_;
    Clock::time_point search_until_{}, next_scan_{};
    Bridge bridge_;
    bool connected_ = false;
};

}  // namespace ua
