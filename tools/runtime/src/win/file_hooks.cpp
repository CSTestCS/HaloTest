#include "file_hooks.hpp"

#include <windows.h>

#include <MinHook.h>
#include <atomic>

#include "app.hpp"
#include "log.hpp"

namespace ua::file_hooks {
namespace {

using CreateFileW_t = HANDLE(WINAPI*)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
using CreateFileA_t = HANDLE(WINAPI*)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
using CreateFile2_t = HANDLE(WINAPI*)(LPCWSTR, DWORD, DWORD, DWORD, LPCREATEFILE2_EXTENDED_PARAMETERS);
using GetAttrEx_t = BOOL(WINAPI*)(LPCWSTR, GET_FILEEX_INFO_LEVELS, LPVOID);

CreateFileW_t real_CreateFileW;
CreateFileA_t real_CreateFileA;
CreateFile2_t real_CreateFile2;
GetAttrEx_t real_GetAttrEx;
thread_local bool in_hook = false;  // our own file access (logging, json) must not recurse

// Returns the pack path for a campaign map, or empty. Notifies the runtime that the map opened.
std::wstring route(LPCWSTR path, bool opening) {
    auto& s = app::state();
    if (!path || !s.redirect || in_hook) return {};
    in_hook = true;
    std::wstring out;
    if (auto hit = s.redirect->match(app::narrow(path))) {
        out = app::widen(hit->path);
        if (opening && s.runtime) {
            s.runtime->on_map_opened(hit->game, Runtime::Clock::now());
            log::line("map " + app::narrow(path) + " -> pack (" + hit->game + ")");
        }
    }
    in_hook = false;
    return out;
}

HANDLE WINAPI hook_CreateFileW(LPCWSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp,
                               DWORD flags, HANDLE tmpl) {
    std::wstring alt = route(name, true);
    return real_CreateFileW(alt.empty() ? name : alt.c_str(), access, share, sa, disp, flags, tmpl);
}

HANDLE WINAPI hook_CreateFileA(LPCSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp,
                               DWORD flags, HANDLE tmpl) {
    if (name) {
        std::wstring alt = route(app::widen(name).c_str(), true);
        if (!alt.empty()) return real_CreateFileW(alt.c_str(), access, share, sa, disp, flags, tmpl);
    }
    return real_CreateFileA(name, access, share, sa, disp, flags, tmpl);
}

HANDLE WINAPI hook_CreateFile2(LPCWSTR name, DWORD access, DWORD share, DWORD disp,
                               LPCREATEFILE2_EXTENDED_PARAMETERS ext) {
    std::wstring alt = route(name, true);
    return real_CreateFile2(alt.empty() ? name : alt.c_str(), access, share, disp, ext);
}

// Size/date queries should describe the file that will actually be opened.
BOOL WINAPI hook_GetAttrEx(LPCWSTR name, GET_FILEEX_INFO_LEVELS level, LPVOID info) {
    std::wstring alt = route(name, false);
    return real_GetAttrEx(alt.empty() ? name : alt.c_str(), level, info);
}

template <typename T>
bool hook(LPCWSTR module, LPCSTR proc, LPVOID detour, T* original) {
    MH_STATUS st = MH_CreateHookApi(module, proc, detour, reinterpret_cast<LPVOID*>(original));
    if (st != MH_OK) {
        log::line(std::string("hook ") + proc + " failed: " + MH_StatusToString(st));
        return false;
    }
    return true;
}

}  // namespace

bool install() {
    bool ok = hook(L"kernelbase", "CreateFileW", reinterpret_cast<LPVOID>(&hook_CreateFileW), &real_CreateFileW);
    ok &= hook(L"kernelbase", "CreateFileA", reinterpret_cast<LPVOID>(&hook_CreateFileA), &real_CreateFileA);
    hook(L"kernelbase", "CreateFile2", reinterpret_cast<LPVOID>(&hook_CreateFile2), &real_CreateFile2);
    hook(L"kernelbase", "GetFileAttributesExW", reinterpret_cast<LPVOID>(&hook_GetAttrEx), &real_GetAttrEx);
    return ok && MH_EnableHook(MH_ALL_HOOKS) == MH_OK;
}

}  // namespace ua::file_hooks
