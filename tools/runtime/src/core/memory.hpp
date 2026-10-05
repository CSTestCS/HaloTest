// Process memory access, abstracted so the bridge can be tested against fake memory.
#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

namespace ua {

struct Region {
    std::uintptr_t base;
    std::size_t size;
};

class Memory {
public:
    virtual ~Memory() = default;
    // Committed, private, readable+writable regions worth scanning.
    virtual std::vector<Region> regions() = 0;
    // Safe accessors: return false instead of faulting when memory went away.
    virtual bool read(std::uintptr_t addr, void* out, std::size_t n) = 0;
    virtual bool write(std::uintptr_t addr, const void* in, std::size_t n) = 0;
};

}  // namespace ua
