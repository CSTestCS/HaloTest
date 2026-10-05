#pragma once
#include <string>

namespace ua::log {
void open(const std::wstring& path);   // UnifiedArmory\runtime.log, truncated per session
void line(const std::string& text);
}  // namespace ua::log
