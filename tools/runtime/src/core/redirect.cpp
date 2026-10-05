#include "redirect.hpp"

#include <algorithm>
#include <cctype>

namespace ua {

std::string normalize_path(const std::string& path) {
    std::string p = path;
    if (p.rfind("\\\\?\\", 0) == 0 || p.rfind("//?/", 0) == 0) p = p.substr(4);
    std::string out;
    out.reserve(p.size());
    for (char ch : p) {
        char c = ch == '\\' ? '/' : static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
        if (c == '/' && !out.empty() && out.back() == '/') continue;
        out.push_back(c);
    }
    return out;
}

Redirector::Redirector(std::string mcc_root, std::string pack_maps,
                       const std::vector<std::pair<std::string, std::string>>& maps)
    : root_(normalize_path(mcc_root)), pack_(std::move(pack_maps)) {
    if (root_.empty() || root_.back() != '/') root_.push_back('/');
    for (const auto& [rel, game] : maps) maps_[normalize_path(rel)] = {rel, game};
}

std::optional<Redirector::Hit> Redirector::match(const std::string& requested) const {
    std::string n = normalize_path(requested);
    if (n.rfind(root_, 0) != 0) return std::nullopt;
    auto it = maps_.find(n.substr(root_.size()));
    if (it == maps_.end()) return std::nullopt;
    std::string rel = it->second.first;
    char sep = pack_.find('\\') != std::string::npos ? '\\' : '/';
    std::replace(rel.begin(), rel.end(), sep == '\\' ? '/' : '\\', sep);
    std::string out = pack_;
    if (!out.empty() && out.back() != sep) out.push_back(sep);
    return Hit{out + rel, it->second.second};
}

}  // namespace ua
