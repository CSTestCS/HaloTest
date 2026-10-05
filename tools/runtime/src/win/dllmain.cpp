// Unified Armory runtime. Loaded by MCC as version.dll (see proxy_version.cpp).
//
// On start it loads the pack manifest and your saved selection, routes campaign map
// opens to the pack, shows the armory overlay (F8) and runs a background thread that
// writes your selection into the running mission's script globals (see core/bridge.hpp).
#include <windows.h>

#include <MinHook.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <thread>

#include "app.hpp"
#include "file_hooks.hpp"
#include "log.hpp"
#include "memory_win.hpp"
#include "overlay.hpp"

namespace ua::app {

State& state() {
    static State s;
    return s;
}

std::string narrow(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), s.data(), n, nullptr, nullptr);
    return s;
}

std::wstring widen(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
}

void save_selection() {
    auto& s = state();
    if (!s.runtime) return;
    std::ofstream f(std::filesystem::path(s.selection_path), std::ios::binary | std::ios::trunc);
    f << dump_selection(s.runtime->selection());
}

}  // namespace ua::app

namespace {

using namespace ua;

std::string read_file(const std::wstring& path) {
    std::ifstream f(std::filesystem::path(path), std::ios::binary);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

std::wstring parent(std::wstring p) {
    auto i = p.find_last_of(L"\\/");
    return i == std::wstring::npos ? p : p.substr(0, i);
}

bool running_under_eac() {
    return GetModuleHandleW(L"EasyAntiCheat_x64.dll") || GetModuleHandleW(L"EasyAntiCheat_EOS.dll");
}

DWORD WINAPI start(LPVOID) {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    std::wstring exe_path = exe;
    std::wstring exe_name = exe_path.substr(exe_path.find_last_of(L"\\/") + 1);
    if (_wcsicmp(exe_name.c_str(), L"MCC-Win64-Shipping.exe") != 0) return 0;  // only a proxy elsewhere

    auto& s = app::state();
    std::wstring bin = parent(exe_path);                  // <MCC>\mcc\binaries\win64
    std::wstring mcc = parent(parent(parent(bin)));       // <MCC>
    s.mod_dir = bin + L"\\UnifiedArmory";
    s.selection_path = s.mod_dir + L"\\selection.json";
    log::open(s.mod_dir + L"\\runtime.log");
    log::line("Unified Armory runtime starting");

    if (running_under_eac()) {
        log::line("Easy Anti-Cheat is active: staying disabled. Launch MCC with mods (EAC off).");
        return 0;
    }
    static WinMemory memory;
    try {
        Manifest m = parse_manifest(read_file(s.mod_dir + L"\\manifest.json"));
        std::vector<std::pair<std::string, std::string>> maps;
        for (const auto& g : m.games)
            for (const auto& rel : g.maps) maps.push_back({rel, g.id});
        s.redirect = std::make_unique<Redirector>(app::narrow(mcc), app::narrow(s.mod_dir + L"\\maps"), maps);
        s.runtime = std::make_unique<Runtime>(memory, std::move(m), parse_selection(read_file(s.selection_path)));
        log::line("pack loaded: " + std::to_string(maps.size()) + " campaign maps");
    } catch (const std::exception& e) {
        s.load_error = std::string("Couldn't load the map pack: ") + e.what();
        log::line(s.load_error);
    }

    if (MH_Initialize() != MH_OK) {
        log::line("MinHook failed to initialize");
        return 0;
    }
    if (s.redirect && !file_hooks::install()) log::line("file hooks failed: campaign maps will not be swapped");
    overlay::install();

    while (true) {
        if (s.runtime) s.runtime->tick(Runtime::Clock::now());
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        // Hooks go in from a separate thread: little may run under the loader lock.
        if (HANDLE t = CreateThread(nullptr, 0, start, nullptr, 0, nullptr)) CloseHandle(t);
    }
    return TRUE;
}
