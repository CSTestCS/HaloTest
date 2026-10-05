#include "log.hpp"

#include <windows.h>

#include <cstdio>
#include <mutex>

namespace ua::log {
namespace {
std::mutex mu;
FILE* file = nullptr;
}  // namespace

void open(const std::wstring& path) {
    std::lock_guard<std::mutex> lock(mu);
    if (!file) _wfopen_s(&file, path.c_str(), L"w");
}

void line(const std::string& text) {
    std::lock_guard<std::mutex> lock(mu);
    if (!file) return;
    SYSTEMTIME t;
    GetLocalTime(&t);
    std::fprintf(file, "%02d:%02d:%02d.%03d %s\n", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds, text.c_str());
    std::fflush(file);
}
}  // namespace ua::log
