#include "selection.hpp"

#include <nlohmann/json.hpp>
#include <algorithm>
#include <set>
#include <stdexcept>

using nlohmann::json;

namespace ua {

const GameInfo* Manifest::game(const std::string& id) const {
    for (const auto& g : games)
        if (g.id == id) return &g;
    return nullptr;
}

const GameInfo* Manifest::game_by_magic(std::uint32_t magic) const {
    for (const auto& g : games)
        if (g.magic == magic) return &g;
    return nullptr;
}

std::vector<PieceEntry> Manifest::options(const std::string& slot) const {
    std::vector<PieceEntry> out;
    std::set<std::string> seen;
    for (const auto& g : games) {
        auto it = g.slots.find(slot);
        if (it == g.slots.end()) continue;
        for (const auto& e : it->second)
            if (e.uid != kOwn && seen.insert(e.uid).second) out.push_back(e);
    }
    return out;
}

std::string Selection::pick(const std::string& game, const std::string& slot) const {
    auto g = overrides.find(game);
    if (g != overrides.end()) {
        auto s = g->second.find(slot);
        if (s != g->second.end() && !s->second.empty()) return s->second;
    }
    auto a = armor.find(slot);
    if (a != armor.end() && !a->second.empty() && a->second != kDefault) return a->second;
    return kOwn;
}

Manifest parse_manifest(const std::string& text) {
    json j = json::parse(text);
    if (j.value("format", "") != "unified-armory-pack/1")
        throw std::runtime_error("manifest.json is not a unified-armory-pack/1 file");
    Manifest m;
    m.seed = j.at("seed").get<std::uint32_t>();
    m.slot_order = j.at("slot_order").get<std::vector<std::string>>();
    m.slot_labels = j.at("slots").get<std::map<std::string, std::string>>();
    if (j.contains("game_names")) m.game_names = j["game_names"].get<std::map<std::string, std::string>>();
    std::vector<GameInfo> games;
    for (auto& [id, g] : j.at("games").items()) {
        GameInfo info;
        info.id = id;
        info.name = g.at("name").get<std::string>();
        info.magic = g.at("magic").get<std::uint32_t>();
        for (auto& [slot, entries] : g.at("slots").items())
            for (auto& e : entries)
                info.slots[slot].push_back({e.at("index").get<int>(), e.at("uid").get<std::string>(),
                                            e.at("name").get<std::string>(), e.value("from", "")});
        info.maps = g.at("maps").get<std::vector<std::string>>();
        games.push_back(std::move(info));
    }
    std::sort(games.begin(), games.end(), [](const GameInfo& a, const GameInfo& b) { return a.magic < b.magic; });
    for (const auto& g : games) m.game_names.emplace(g.id, g.name);
    m.games = std::move(games);
    return m;
}

Selection parse_selection(const std::string& text) {
    Selection s;
    try {
        json j = json::parse(text);
        if (j.contains("armor")) s.armor = j["armor"].get<std::map<std::string, std::string>>();
        if (j.contains("overrides"))
            s.overrides = j["overrides"].get<std::map<std::string, std::map<std::string, std::string>>>();
    } catch (const std::exception&) {
        return {};
    }
    return s;
}

std::string dump_selection(const Selection& s) {
    json j{{"armor", s.armor}, {"overrides", s.overrides}};
    return j.dump(2);
}

std::vector<std::int32_t> resolve(const Manifest& m, const Selection& s, const std::string& game) {
    std::vector<std::int32_t> out(m.slot_order.size(), 0);
    const GameInfo* g = m.game(game);
    if (!g) return out;
    for (std::size_t k = 0; k < m.slot_order.size(); ++k) {
        const std::string& slot = m.slot_order[k];
        std::string uid = s.pick(game, slot);
        auto it = g->slots.find(slot);
        if (it == g->slots.end()) continue;
        for (const auto& e : it->second)
            if (e.uid == uid) out[k] = e.index;
    }
    return out;
}

}  // namespace ua
