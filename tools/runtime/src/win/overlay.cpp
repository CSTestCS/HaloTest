// The armory panel: Dear ImGui drawn on MCC's own DirectX 11 swap chain, over the main menu
// or in game. F8 toggles it; while it's open, mouse and keyboard go to the panel only.
#include "overlay.hpp"

#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>

#include <MinHook.h>
#include <chrono>
#include <string>

#include "app.hpp"
#include "imgui.h"
#include "imgui_impl_dx11.h"
#include "imgui_impl_win32.h"
#include "log.hpp"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

namespace ua::overlay {
namespace {

using Present_t = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT);
using Resize_t = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT, UINT, DXGI_FORMAT, UINT);

Present_t real_present;
Resize_t real_resize;
ID3D11Device* device;
ID3D11DeviceContext* context;
ID3D11RenderTargetView* rtv;
HWND window;
WNDPROC real_wndproc;
bool ready, visible;
std::chrono::steady_clock::time_point hint_until;
constexpr UINT kToggleKey = VK_F8;

bool is_input(UINT msg) {
    return (msg >= WM_MOUSEFIRST && msg <= WM_MOUSELAST) || (msg >= WM_KEYFIRST && msg <= WM_KEYLAST) ||
           msg == WM_INPUT || msg == WM_CHAR;
}

LRESULT CALLBACK wndproc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_KEYDOWN && wp == kToggleKey && !(lp & (1 << 30))) {
        visible = !visible;
        return 0;
    }
    if (visible) {
        ImGui_ImplWin32_WndProcHandler(hwnd, msg, wp, lp);
        if (is_input(msg)) return msg == WM_INPUT ? DefWindowProcW(hwnd, msg, wp, lp) : 0;
    }
    return CallWindowProcW(real_wndproc, hwnd, msg, wp, lp);
}

void make_rtv(IDXGISwapChain* swap) {
    ID3D11Texture2D* back = nullptr;
    if (SUCCEEDED(swap->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&back))) && back) {
        device->CreateRenderTargetView(back, nullptr, &rtv);
        back->Release();
    }
}

bool init(IDXGISwapChain* swap) {
    if (FAILED(swap->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&device)))) return false;
    device->GetImmediateContext(&context);
    DXGI_SWAP_CHAIN_DESC desc;
    swap->GetDesc(&desc);
    window = desc.OutputWindow;
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    ImGui::StyleColorsDark();
    ImGuiStyle& st = ImGui::GetStyle();
    st.WindowRounding = 8.f;
    st.FrameRounding = 4.f;
    st.Colors[ImGuiCol_TitleBgActive] = ImVec4(0.29f, 0.42f, 0.18f, 1.f);
    st.Colors[ImGuiCol_Header] = ImVec4(0.29f, 0.42f, 0.18f, 0.6f);
    st.Colors[ImGuiCol_HeaderHovered] = ImVec4(0.36f, 0.52f, 0.22f, 0.8f);
    st.Colors[ImGuiCol_Tab] = ImVec4(0.20f, 0.28f, 0.14f, 1.f);
    st.Colors[ImGuiCol_TabSelected] = ImVec4(0.36f, 0.52f, 0.22f, 1.f);
    st.Colors[ImGuiCol_TabHovered] = ImVec4(0.42f, 0.60f, 0.26f, 1.f);
    ImGui_ImplWin32_Init(window);
    ImGui_ImplDX11_Init(device, context);
    real_wndproc = reinterpret_cast<WNDPROC>(SetWindowLongPtrW(window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(wndproc)));
    make_rtv(swap);
    hint_until = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    log::line("overlay ready");
    return true;
}

std::string source_label(const Manifest& m, const PieceEntry& e) {
    auto it = m.game_names.find(e.from);
    return e.name + "  (" + (it != m.game_names.end() ? it->second : e.from) + ")";
}

// One combo; returns true when the value changed. `value` is a uid, "" (inherit) or "default"/"own".
bool piece_combo(const char* id, const std::string& label, std::string& value,
                 const std::vector<std::pair<std::string, std::string>>& choices) {
    std::string current = choices.empty() ? "" : choices.front().second;
    for (const auto& [uid, text] : choices)
        if (uid == value) current = text;
    bool changed = false;
    ImGui::TextUnformatted(label.c_str());
    ImGui::SameLine(150);
    ImGui::SetNextItemWidth(-1);
    if (ImGui::BeginCombo(id, current.c_str())) {
        for (const auto& [uid, text] : choices) {
            bool selected = uid == value;
            if (ImGui::Selectable(text.c_str(), selected)) {
                value = uid;
                changed = true;
            }
            if (selected) ImGui::SetItemDefaultFocus();
        }
        ImGui::EndCombo();
    }
    return changed;
}

void draw_panel() {
    auto& s = app::state();
    ImGui::SetNextWindowSize(ImVec2(620, 520), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowPos(ImVec2(80, 80), ImGuiCond_FirstUseEver);
    if (!ImGui::Begin("Unified Armory", &visible, ImGuiWindowFlags_NoCollapse)) {
        ImGui::End();
        return;
    }
    if (!s.runtime) {
        ImGui::TextWrapped("%s", s.load_error.empty() ? "The map pack isn't installed." : s.load_error.c_str());
        ImGui::TextWrapped("Build it with the Unified Armory builder, then restart MCC.");
        ImGui::End();
        return;
    }
    const Manifest& m = s.runtime->manifest();
    Selection sel = s.runtime->selection();
    bool changed = false;

    auto st = s.runtime->status();
    if (st.game.empty())
        ImGui::TextDisabled("Pick your armor, then start any campaign mission. Changes apply live.");
    else {
        const GameInfo* g = m.game(st.game);
        std::string name = g ? g->name : st.game;
        if (st.connected)
            ImGui::TextColored(ImVec4(0.5f, 0.85f, 0.5f, 1), "%s: armor applied", name.c_str());
        else if (st.searching)
            ImGui::TextColored(ImVec4(0.95f, 0.8f, 0.4f, 1), "%s: connecting to the mission...", name.c_str());
        else
            ImGui::TextColored(ImVec4(0.95f, 0.55f, 0.45f, 1), "%s: couldn't connect (see runtime.log)", name.c_str());
    }
    ImGui::Separator();

    if (ImGui::BeginTabBar("games")) {
        if (ImGui::BeginTabItem("All games")) {
            ImGui::TextDisabled("Worn in every campaign unless a game's tab says otherwise.");
            for (const auto& slot : m.slot_order) {
                std::vector<std::pair<std::string, std::string>> choices{{kDefault, "Each game's own"}};
                for (const auto& e : m.options(slot)) choices.push_back({e.uid, source_label(m, e)});
                std::string v = sel.armor.count(slot) ? sel.armor[slot] : kDefault;
                if (piece_combo(("##all_" + slot).c_str(), m.slot_labels.at(slot), v, choices)) {
                    sel.armor[slot] = v;
                    changed = true;
                }
            }
            ImGui::EndTabItem();
        }
        for (const auto& g : m.games) {
            if (!ImGui::BeginTabItem(g.name.c_str())) continue;
            ImGui::TextDisabled("Only for %s.", g.name.c_str());
            for (const auto& slot : m.slot_order) {
                std::vector<std::pair<std::string, std::string>> choices{{"", "Same as All games"},
                                                                        {kOwn, g.name + "'s own"}};
                auto it = g.slots.find(slot);
                if (it != g.slots.end())
                    for (const auto& e : it->second)
                        if (e.uid != kOwn) choices.push_back({e.uid, source_label(m, e)});
                std::string v = sel.overrides[g.id].count(slot) ? sel.overrides[g.id][slot] : "";
                if (piece_combo(("##" + g.id + "_" + slot).c_str(), m.slot_labels.at(slot), v, choices)) {
                    if (v.empty()) sel.overrides[g.id].erase(slot);
                    else sel.overrides[g.id][slot] = v;
                    changed = true;
                }
            }
            ImGui::EndTabItem();
        }
        ImGui::EndTabBar();
    }
    ImGui::Separator();
    ImGui::TextDisabled("F8 closes this panel. Your choices are saved automatically.");
    ImGui::End();

    if (changed) {
        s.runtime->set_selection(sel);
        app::save_selection();
    }
}

HRESULT STDMETHODCALLTYPE hook_present(IDXGISwapChain* swap, UINT sync, UINT flags) {
    if (!ready) ready = init(swap);
    bool hint = std::chrono::steady_clock::now() < hint_until;
    if (ready && (visible || hint)) {
        if (!rtv) make_rtv(swap);
        ImGui::GetIO().MouseDrawCursor = visible;
        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();
        if (visible)
            draw_panel();
        else {
            ImGui::SetNextWindowPos(ImVec2(20, 20));
            ImGui::SetNextWindowBgAlpha(0.6f);
            ImGui::Begin("##hint", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                                                ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoSavedSettings);
            ImGui::TextUnformatted("Unified Armory: press F8 to choose your armor");
            ImGui::End();
        }
        ImGui::Render();
        context->OMSetRenderTargets(1, &rtv, nullptr);
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
    }
    return real_present(swap, sync, flags);
}

HRESULT STDMETHODCALLTYPE hook_resize(IDXGISwapChain* swap, UINT count, UINT w, UINT h, DXGI_FORMAT fmt, UINT flags) {
    if (rtv) {
        rtv->Release();
        rtv = nullptr;
    }
    return real_resize(swap, count, w, h, fmt, flags);
}

}  // namespace

bool install() {
    // A throwaway device + swap chain gives us the IDXGISwapChain vtable shared by MCC's.
    WNDCLASSEXW wc{sizeof wc, CS_CLASSDC, DefWindowProcW, 0, 0, GetModuleHandleW(nullptr), nullptr, nullptr, nullptr,
                   nullptr, L"UnifiedArmoryDummy", nullptr};
    RegisterClassExW(&wc);
    HWND hwnd = CreateWindowW(wc.lpszClassName, L"", WS_OVERLAPPEDWINDOW, 0, 0, 100, 100, nullptr, nullptr,
                              wc.hInstance, nullptr);
    DXGI_SWAP_CHAIN_DESC sd{};
    sd.BufferCount = 1;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow = hwnd;
    sd.SampleDesc.Count = 1;
    sd.Windowed = TRUE;
    IDXGISwapChain* swap = nullptr;
    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    D3D_FEATURE_LEVEL level;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                               D3D11_SDK_VERSION, &sd, &swap, &dev, &level, &ctx);
    bool ok = false;
    if (SUCCEEDED(hr) && swap) {
        void** vtable = *reinterpret_cast<void***>(swap);
        ok = MH_CreateHook(vtable[8], reinterpret_cast<LPVOID>(&hook_present), reinterpret_cast<LPVOID*>(&real_present)) == MH_OK &&
             MH_CreateHook(vtable[13], reinterpret_cast<LPVOID>(&hook_resize), reinterpret_cast<LPVOID*>(&real_resize)) == MH_OK &&
             MH_EnableHook(vtable[8]) == MH_OK && MH_EnableHook(vtable[13]) == MH_OK;
    }
    if (swap) swap->Release();
    if (ctx) ctx->Release();
    if (dev) dev->Release();
    DestroyWindow(hwnd);
    UnregisterClassW(wc.lpszClassName, wc.hInstance);
    log::line(ok ? "overlay hooks installed" : "overlay hooks failed: the F8 panel won't appear");
    return ok;
}

}  // namespace ua::overlay
