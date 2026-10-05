// Process-wide state shared by the hooks, the overlay and the background thread.
#pragma once
#include <memory>
#include <string>

#include "redirect.hpp"
#include "runtime.hpp"

namespace ua::app {

struct State {
    std::wstring mod_dir;          // <MCC>\mcc\binaries\win64\UnifiedArmory
    std::wstring selection_path;   // <mod_dir>\selection.json
    std::unique_ptr<Runtime> runtime;
    std::unique_ptr<Redirector> redirect;
    std::string load_error;        // shown in the overlay when the pack couldn't be loaded
};

State& state();
void save_selection();
std::string narrow(const std::wstring& w);
std::wstring widen(const std::string& s);

}  // namespace ua::app
