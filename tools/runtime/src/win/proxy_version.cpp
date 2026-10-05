// version.dll proxy: MCC loads this copy from its own folder; every export forwards to the
// real system version.dll so the game behaves exactly as without the mod.
#include <windows.h>

namespace {
HMODULE real() {
    static HMODULE h = [] {
        wchar_t path[MAX_PATH];
        GetSystemDirectoryW(path, MAX_PATH);
        lstrcatW(path, L"\\version.dll");
        return LoadLibraryW(path);
    }();
    return h;
}

template <typename F>
F fn(const char* name) {
    return reinterpret_cast<F>(GetProcAddress(real(), name));
}
}  // namespace

#define UA_FORWARD(ret, name, params, args)                                  \
    extern "C" ret WINAPI ua_##name params {                                  \
        using F = ret(WINAPI*) params;                                       \
        static F real_fn = fn<F>(#name);                                           \
        return real_fn args;                                                     \
    }

UA_FORWARD(BOOL, GetFileVersionInfoA, (LPCSTR a, DWORD b, DWORD c, LPVOID d), (a, b, c, d))
UA_FORWARD(BOOL, GetFileVersionInfoW, (LPCWSTR a, DWORD b, DWORD c, LPVOID d), (a, b, c, d))
UA_FORWARD(BOOL, GetFileVersionInfoExA, (DWORD f, LPCSTR a, DWORD b, DWORD c, LPVOID d), (f, a, b, c, d))
UA_FORWARD(BOOL, GetFileVersionInfoExW, (DWORD f, LPCWSTR a, DWORD b, DWORD c, LPVOID d), (f, a, b, c, d))
UA_FORWARD(DWORD, GetFileVersionInfoSizeA, (LPCSTR a, LPDWORD b), (a, b))
UA_FORWARD(DWORD, GetFileVersionInfoSizeW, (LPCWSTR a, LPDWORD b), (a, b))
UA_FORWARD(DWORD, GetFileVersionInfoSizeExA, (DWORD f, LPCSTR a, LPDWORD b), (f, a, b))
UA_FORWARD(DWORD, GetFileVersionInfoSizeExW, (DWORD f, LPCWSTR a, LPDWORD b), (f, a, b))
UA_FORWARD(int, GetFileVersionInfoByHandle, (DWORD a, HANDLE b, LPVOID c, DWORD d), (a, b, c, d))
UA_FORWARD(DWORD, VerFindFileA, (DWORD a, LPCSTR b, LPCSTR c, LPCSTR d, LPSTR e, PUINT f, LPSTR g, PUINT h),
           (a, b, c, d, e, f, g, h))
UA_FORWARD(DWORD, VerFindFileW, (DWORD a, LPCWSTR b, LPCWSTR c, LPCWSTR d, LPWSTR e, PUINT f, LPWSTR g, PUINT h),
           (a, b, c, d, e, f, g, h))
UA_FORWARD(DWORD, VerInstallFileA, (DWORD a, LPCSTR b, LPCSTR c, LPCSTR d, LPCSTR e, LPCSTR f, LPSTR g, PUINT h),
           (a, b, c, d, e, f, g, h))
UA_FORWARD(DWORD, VerInstallFileW,
           (DWORD a, LPCWSTR b, LPCWSTR c, LPCWSTR d, LPCWSTR e, LPCWSTR f, LPWSTR g, PUINT h),
           (a, b, c, d, e, f, g, h))
UA_FORWARD(DWORD, VerLanguageNameA, (DWORD a, LPSTR b, DWORD c), (a, b, c))
UA_FORWARD(DWORD, VerLanguageNameW, (DWORD a, LPWSTR b, DWORD c), (a, b, c))
UA_FORWARD(BOOL, VerQueryValueA, (LPCVOID a, LPCSTR b, LPVOID* c, PUINT d), (a, b, c, d))
UA_FORWARD(BOOL, VerQueryValueW, (LPCVOID a, LPCWSTR b, LPVOID* c, PUINT d), (a, b, c, d))
