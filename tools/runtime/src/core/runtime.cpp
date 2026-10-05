#include "runtime.hpp"

namespace ua {

Runtime::Runtime(Memory& mem, Manifest manifest, Selection selection)
    : mem_(mem), manifest_(std::move(manifest)), selection_(std::move(selection)), bridge_(mem) {}

void Runtime::on_map_opened(const std::string& game, Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mu_);
    game_ = game;
    search_until_ = now + kSearchWindow;
    next_scan_ = now;
}

void Runtime::tick(Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mu_);
    const GameInfo* g = manifest_.game(game_);
    if (!g) return;
    BridgeTarget target{g->magic, manifest_.seed, static_cast<int>(manifest_.slot_order.size())};
    // Each tick validates the candidates (dropping stale ones) and writes the selection to live ones.
    // A full scan runs while searching and nothing live is known. A candidate needs two ticks to be
    // seen toggling, so a scan never discards candidates that are still waiting to go live.
    bridge_.update(target, resolve(manifest_, selection_, game_));
    connected_ = bridge_.connected();
    if (!connected_ && bridge_.candidates().empty() && now < search_until_ && now >= next_scan_) {
        bridge_.scan(target);
        next_scan_ = now + kScanInterval;
    }
    if (connected_) search_until_ = now + kSearchWindow;  // keep following checkpoints/reloads
}

Selection Runtime::selection() const {
    std::lock_guard<std::mutex> lock(mu_);
    return selection_;
}

void Runtime::set_selection(Selection s) {
    std::lock_guard<std::mutex> lock(mu_);
    selection_ = std::move(s);
}

Runtime::Status Runtime::status() const {
    std::lock_guard<std::mutex> lock(mu_);
    Clock::time_point now = Clock::now();
    return {game_, connected_, !connected_ && !game_.empty() && now < search_until_};
}

}  // namespace ua
