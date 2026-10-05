// Integration test for the real version.dll, run as a stand-in "MCC-Win64-Shipping.exe"
// (on Windows, or under Wine in CI). Layout prepared by tests/run_win_harness.sh:
//   <root>/halo3/maps/010_jungle.map                       "ORIGINAL"
//   <root>/mcc/binaries/win64/MCC-Win64-Shipping.exe        this program
//   <root>/mcc/binaries/win64/version.dll                   the mod
//   <root>/mcc/binaries/win64/UnifiedArmory/manifest.json, selection.json
//   <root>/mcc/binaries/win64/UnifiedArmory/maps/halo3/maps/010_jungle.map   "PACK"
#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <string>

static int failures = 0;
static void check(bool ok, const char* what) {
    std::printf("%s %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) ++failures;
}

static std::string read_via_createfile(const std::wstring& path) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return "<open failed>";
    char buf[64] = {};
    DWORD got = 0;
    ReadFile(h, buf, sizeof buf - 1, &got, nullptr);
    CloseHandle(h);
    return std::string(buf, got);
}

int main() {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    std::wstring bin = exe;
    bin = bin.substr(0, bin.find_last_of(L"\\/"));
    std::wstring root = bin.substr(0, bin.find_last_of(L"\\/"));   // binaries
    root = root.substr(0, root.find_last_of(L"\\/"));              // mcc
    root = root.substr(0, root.find_last_of(L"\\/"));              // <root>

    // 1. The proxy forwards to the real version.dll (imported by this exe, so the local copy loads).
    DWORD handle = 0;
    wchar_t sys[MAX_PATH];
    GetSystemDirectoryW(sys, MAX_PATH);
    std::wstring k32 = std::wstring(sys) + L"\\kernel32.dll";
    check(GetFileVersionInfoSizeW(k32.c_str(), &handle) > 0, "version.dll calls are forwarded to the system copy");
    check(GetModuleHandleW(L"version.dll") != nullptr && [&] {
        wchar_t p[MAX_PATH];
        GetModuleFileNameW(GetModuleHandleW(L"version.dll"), p, MAX_PATH);
        return std::wstring(p).find(bin) == 0;
    }(), "the mod's version.dll (not the system one) is loaded");

    Sleep(1500);  // the runtime starts on its own thread

    // 2. Mission script globals as the engine would hold them: stride 8, value at offset 0.
    //    magic, echo, helmet, chest, shoulder_left, shoulder_right, wrist, utility, knees, tick, seed
    const std::uint32_t MAGIC = 0x55410003, SEED = 0x41524D59;
    const int SLOTS = 7;
    auto* g = static_cast<std::uint8_t*>(VirtualAlloc(nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    auto at = [&](int i) { return reinterpret_cast<std::uint32_t*>(g + 0x100 + 8 * i); };
    *at(0) = MAGIC;
    *at(1) = 0;
    for (int k = 0; k < SLOTS; ++k) *at(2 + k) = 0;
    *at(2 + SLOTS) = 0;
    *at(3 + SLOTS) = SEED;

    // 3. Opening the campaign map gets the pack's copy (and tells the runtime a Halo 3 mission is loading).
    std::wstring map = root + L"\\halo3\\maps\\010_jungle.map";
    check(read_via_createfile(map).rfind("PACK", 0) == 0, "campaign map open is redirected to the pack");
    std::wstring other = root + L"\\halo3\\maps\\shared.map";
    check(read_via_createfile(other).rfind("ORIGINAL", 0) == 0, "other files are left alone");

    // 4. The mission script starts (echo := seed) and its loop flips ua_tick; the runtime should
    //    find the globals and write the selection: helmet = Gungnir (option 2), chest = EOD (option 1).
    *at(1) = SEED;
    bool written = false;
    for (int i = 0; i < 60 && !written; ++i) {
        *at(2 + SLOTS) ^= 1;
        Sleep(250);
        written = *at(2) == 2 && *at(3) == 1;
    }
    check(written, "selection is written into the running mission's script globals");
    check(*at(4) == 0 && *at(0) == MAGIC && *at(1) == SEED && *at(3 + SLOTS) == SEED, "nothing else is touched");

    std::printf(failures ? "%d FAILED\n" : "ALL PASSED\n", failures);
    return failures ? 1 : 0;
}
