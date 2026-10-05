// Decides which file opens to send to the map pack: MCC asks for
// <MCC>/halo3/maps/010_jungle.map and gets <MCC>/mcc/binaries/win64/UnifiedArmory/maps/halo3/maps/010_jungle.map.
// MCC's own files are never modified.
#pragma once
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace ua {

std::string normalize_path(const std::string& path);  // lowercase, '/', no \\?\ prefix, no duplicate '/'

class Redirector {
public:
    // mcc_root: the MCC install folder; pack_maps: <mod>/maps; maps: MCC-relative paths ("halo3/maps/010_jungle.map")
    // with the game each belongs to.
    Redirector(std::string mcc_root, std::string pack_maps, const std::vector<std::pair<std::string, std::string>>& maps);

    struct Hit {
        std::string path;  // the pack's file, in the original path style
        std::string game;
    };
    std::optional<Hit> match(const std::string& requested) const;

private:
    std::string root_;   // normalized, trailing '/'
    std::string pack_;   // as given, used to build the redirected path
    std::unordered_map<std::string, std::pair<std::string, std::string>> maps_;  // rel -> (rel as given, game)
};

}  // namespace ua
