#pragma once
#include "memory.hpp"

namespace ua {
// The current process's own memory. Reads and writes go through Read/WriteProcessMemory so
// memory freed under us (a mission unloading) fails cleanly instead of crashing the game.
class WinMemory : public Memory {
public:
    std::vector<Region> regions() override;
    bool read(std::uintptr_t addr, void* out, std::size_t n) override;
    bool write(std::uintptr_t addr, const void* in, std::size_t n) override;
};
}  // namespace ua
