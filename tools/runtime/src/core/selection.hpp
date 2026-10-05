// The pack manifest (what each game's maps contain) and the player's selection.
#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace ua {

inline constexpr const char* kOwn = "own";          // a game's own armor (option 0)
inline constexpr const char* kDefault = "default";  // same as own, in profiles from the builder

struct PieceEntry {
    int index = 0;
    std::string uid;   // "reach/helmet_gungnir", or "own"
    std::string name;
    std::string from;  // source game id
};

struct GameInfo {
    std::string id;
    std::string name;
    std::uint32_t magic = 0;
    std::map<std::string, std::vector<PieceEntry>> slots;
    std::vector<std::string> maps;  // MCC-relative, e.g. "halo3/maps/010_jungle.map"
};

struct Manifest {
    std::uint32_t seed = 0;
    std::vector<std::string> slot_order;
    std::map<std::string, std::string> slot_labels;
    std::vector<GameInfo> games;  // in game order
    std::map<std::string, std::string> game_names;  // every game id -> display name (pieces' "from")

    const GameInfo* game(const std::string& id) const;
    const GameInfo* game_by_magic(std::uint32_t magic) const;
    // Every piece offered for a slot by any game, in game order, without duplicates.
    std::vector<PieceEntry> options(const std::string& slot) const;
};

struct Selection {
    std::map<std::string, std::string> armor;                                // slot -> uid
    std::map<std::string, std::map<std::string, std::string>> overrides;     // game -> slot -> uid

    // The uid a game wears in a slot ("own" when unset).
    std::string pick(const std::string& game, const std::string& slot) const;
};

Manifest parse_manifest(const std::string& json_text);  // throws std::runtime_error
Selection parse_selection(const std::string& json_text);  // tolerant: bad input -> empty selection
std::string dump_selection(const Selection& s);

// Slot values to write for a game, in manifest slot order (0 = own when a piece isn't in that game's pack).
std::vector<std::int32_t> resolve(const Manifest& m, const Selection& s, const std::string& game);

}  // namespace ua
