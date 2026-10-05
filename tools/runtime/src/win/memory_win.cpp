#include "memory_win.hpp"

#include <windows.h>

namespace ua {

std::vector<Region> WinMemory::regions() {
    std::vector<Region> out;
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    auto addr = reinterpret_cast<std::uintptr_t>(si.lpMinimumApplicationAddress);
    const auto end = reinterpret_cast<std::uintptr_t>(si.lpMaximumApplicationAddress);
    MEMORY_BASIC_INFORMATION mbi;
    while (addr < end && VirtualQuery(reinterpret_cast<LPCVOID>(addr), &mbi, sizeof mbi) == sizeof mbi) {
        const DWORD prot = mbi.Protect & 0xFF;
        // Script globals live in the game's heap: committed, private, plain read/write.
        if (mbi.State == MEM_COMMIT && mbi.Type == MEM_PRIVATE && (prot == PAGE_READWRITE) &&
            !(mbi.Protect & (PAGE_GUARD | PAGE_NOCACHE | PAGE_WRITECOMBINE)))
            out.push_back({reinterpret_cast<std::uintptr_t>(mbi.BaseAddress), mbi.RegionSize});
        addr = reinterpret_cast<std::uintptr_t>(mbi.BaseAddress) + mbi.RegionSize;
    }
    return out;
}

bool WinMemory::read(std::uintptr_t addr, void* out, std::size_t n) {
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<LPCVOID>(addr), out, n, &got) && got == n;
}

bool WinMemory::write(std::uintptr_t addr, const void* in, std::size_t n) {
    SIZE_T put = 0;
    return WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<LPVOID>(addr), in, n, &put) && put == n;
}

}  // namespace ua
