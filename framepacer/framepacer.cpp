// SPDX-License-Identifier: MIT
// FramePacer — limitador/ritmador de FPS universal: Vulkan + OpenGL (GLX/EGL).
//
// Uma única biblioteca (libframepacer.so) funciona de dois jeitos ao mesmo tempo:
//   * camada Vulkan implícita (vkQueuePresentKHR)        -> DXVK, VKD3D, jogos Vulkan
//   * LD_PRELOAD: hooks de glXSwapBuffers/eglSwapBuffers -> OpenGL (Minecraft Java,
//     jogos nativos, Wine/WineD3D, SDL/GLFW...)
// Como as duas "portas" são o mesmo .so, compartilham o mesmo estado e o mesmo
// arquivo de controle, e um guard por thread evita pacing duplicado (ex.: Zink,
// que chama vkQueuePresentKHR de dentro de eglSwapBuffers).
//
// O limite é lido a cada frame de $XDG_RUNTIME_DIR/framepacer/<pid>.shm, então
// qualquer processo (GUI, CLI) altera em tempo real, sem reiniciar o jogo.

#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#include <dlfcn.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <mutex>
#include <string>
#include <unordered_map>

#define FP_EXPORT extern "C" __attribute__((visibility("default")))

#if defined(__x86_64__) || defined(__i386__)
#define FP_CPU_RELAX() __builtin_ia32_pause()
#else
#define FP_CPU_RELAX() ((void)0)
#endif

// ---------------------------------------------------------------------------
// Hooks de OpenGL exportados (resolvem por interposição via LD_PRELOAD)
// ---------------------------------------------------------------------------
FP_EXPORT void glXSwapBuffers(void* dpy, unsigned long drawable);
FP_EXPORT void* glXGetProcAddress(const unsigned char* name);
FP_EXPORT void* glXGetProcAddressARB(const unsigned char* name);
FP_EXPORT unsigned int eglSwapBuffers(void* dpy, void* surface);
FP_EXPORT unsigned int eglSwapBuffersWithDamageKHR(void* dpy, void* surface, const int* rects, int n);
FP_EXPORT unsigned int eglSwapBuffersWithDamageEXT(void* dpy, void* surface, const int* rects, int n);
FP_EXPORT void* eglGetProcAddress(const char* name);
// "dlsym" via asm label: evita conflito com a declaração (noexcept) do dlfcn.h
extern "C" __attribute__((visibility("default"))) void* fp_dlsym(void* handle, const char* name) __asm__("dlsym");

namespace {

// ---------------------------------------------------------------------------
// Memória compartilhada com a GUI/CLI (64 bytes, layout fixo — ver framepacer.py)
// ---------------------------------------------------------------------------
constexpr uint32_t kMagic = 0x43415046;  // "FPAC" (little-endian)
enum : uint32_t { API_VULKAN = 1, API_GLX = 2, API_EGL = 4 };

constexpr size_t FT_N = 120;  // amostras no ring de frame times

struct Shared {
    uint32_t magic;
    uint32_t version;                      // 2 (v1 = 64 bytes, sem ring)
    uint32_t pid;
    uint32_t api_mask;                     // bits API_* já vistos neste processo
    std::atomic<int32_t> limit;            // FPS alvo; <= 0 = sem limite
    std::atomic<uint32_t> fps_x100;        // FPS medido * 100
    std::atomic<uint64_t> heartbeat_ns;    // CLOCK_MONOTONIC da última medição
    char name[32];
    std::atomic<uint32_t> ft_head;         // próxima posição do ring
    uint32_t ft_count;                     // amostras válidas (<= FT_N; só a lib escreve)
    uint16_t ft_pad[2];
    std::atomic<uint16_t> ft[FT_N];        // intervalos present-a-present, em µs (teto 65535)
};
// 76 + 2*FT_N sofre padding até múltiplo de 8 (alinhamento do atomic<uint64_t>)
static_assert(sizeof(Shared) == ((76 + 2 * FT_N + 7) & ~size_t(7)), "layout do Shared mudou");

struct Pacer {
    std::once_flag once;
    Shared* shm = nullptr;
    std::mutex mu;
    uint64_t next_ns = 0;
    uint64_t win_start = 0;
    uint32_t win_frames = 0;
    uint64_t last_check_ns = 0;  // última verificação de que o .shm ainda existe
    uint64_t last_present_ns = 0;  // present anterior (p/ o ring de frame times)
    uint64_t spin_ns = 200000;  // espera ativa nos últimos 200 µs (precisão)
};

Pacer& pacer() {
    static Pacer* p = new Pacer;  // nunca destruído: evita corrida no exit()
    return *p;
}

char g_shm_path[512] = {0};
thread_local int tl_depth = 0;  // >0 = já dentro de um present/swap desta lib

inline uint64_t now_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}

bool try_read_int_file(const std::string& path, int* out) {
    FILE* f = fopen(path.c_str(), "r");
    if (!f) return false;
    int v = 0;
    bool ok = (fscanf(f, "%d", &v) == 1);
    fclose(f);
    if (ok) *out = v;
    return ok;
}

// mkdir -p: o dir padrão (~/.local/state/framepacer) pode ainda não existir.
bool mkpath(const std::string& dir) {
    std::string cur;
    for (char c : dir) {
        cur += c;
        if (c == '/' && cur.size() > 1) {
            if (mkdir(cur.c_str(), 0700) != 0 && errno != EEXIST) return false;
        }
    }
    if (!cur.empty() && mkdir(cur.c_str(), 0700) != 0 && errno != EEXIST) return false;
    return true;
}

// Dir compartilhado com GUI/CLI. Em ~/.local/state para atravessar o container
// dos jogos Proton (o pressure-vessel mascara $XDG_RUNTIME_DIR e as pastas
// vulkan/implicit_layer.d, mas compartilha o $HOME).
std::string shared_dir() {
    if (const char* e = getenv("FRAMEPACER_DIR"); e && *e) return e;
    if (const char* s = getenv("XDG_STATE_HOME"); s && *s) return std::string(s) + "/framepacer";
    if (const char* h = getenv("HOME"); h && *h) return std::string(h) + "/.local/state/framepacer";
    if (const char* xdg = getenv("XDG_RUNTIME_DIR"); xdg && *xdg) return std::string(xdg) + "/framepacer";
    return "/tmp/framepacer-" + std::to_string(getuid());
}

// Local antigo (antes do suporte a Proton); só leitura do "default" na migração.
std::string legacy_dir() {
    if (const char* xdg = getenv("XDG_RUNTIME_DIR"); xdg && *xdg) return std::string(xdg) + "/framepacer";
    return "/tmp/framepacer-" + std::to_string(getuid());
}

void do_init() {
    Pacer& g = pacer();
    if (const char* d = getenv("DISABLE_FRAMEPACER"); d && *d && *d != '0') return;

    const std::string dir = shared_dir();
    if (!mkpath(dir)) return;

    if (const char* spin = getenv("FRAMEPACER_SPIN_US")) {
        long long us = atoll(spin);
        if (us < 0) us = 0;
        if (us > 2000) us = 2000;
        g.spin_ns = uint64_t(us) * 1000ull;
    }

    std::string path = dir + "/" + std::to_string(getpid()) + ".shm";
    int fd = open(path.c_str(), O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) return;
    if (ftruncate(fd, sizeof(Shared)) != 0) { close(fd); return; }
    void* p = mmap(nullptr, sizeof(Shared), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (p == MAP_FAILED) return;

    auto* s = static_cast<Shared*>(p);
    s->version = 2;
    s->pid = uint32_t(getpid());

    FILE* comm = fopen("/proc/self/comm", "r");
    if (comm) {
        char buf[32] = {0};
        if (fgets(buf, sizeof buf, comm)) {
            buf[strcspn(buf, "\n")] = 0;
            memcpy(s->name, buf, strnlen(buf, sizeof s->name - 1));
        }
        fclose(comm);
    }

    // Limite inicial: FRAMEPACER_FPS > arquivo "default" (definido na GUI) > sem limite
    int initial = 0;
    if (!try_read_int_file(dir + "/default", &initial))
        try_read_int_file(legacy_dir() + "/default", &initial);  // migração do local antigo
    if (const char* e = getenv("FRAMEPACER_FPS")) initial = atoi(e);
    s->limit.store(initial, std::memory_order_relaxed);

    std::atomic_thread_fence(std::memory_order_release);
    s->magic = kMagic;  // por último: a GUI só enxerga o arquivo depois disso
    g.shm = s;

    snprintf(g_shm_path, sizeof g_shm_path, "%s", path.c_str());
    std::atexit([] { if (g_shm_path[0]) unlink(g_shm_path); });
}

void init_once() { do_init(); }

// A GUI remove .shm de jogos que parecem mortos; se este processo segue vivo
// (ex.: jogo em container que ficou um tempo sem apresentar), recria o arquivo.
void reinit_if_unlinked(uint64_t now) {
    Pacer& g = pacer();
    if (now - g.last_check_ns < 5000000000ull) return;
    std::lock_guard<std::mutex> lk(g.mu);
    if (now - g.last_check_ns < 5000000000ull) return;  // outra thread já tratou
    g.last_check_ns = now;
    if (!g.shm) { do_init(); return; }  // primeira init falhou? tenta de novo (raro)
    if (g_shm_path[0] && access(g_shm_path, F_OK) == 0) return;
    munmap(g.shm, sizeof(Shared));
    g.shm = nullptr;
    g.next_ns = 0;
    g.win_start = 0;
    g.win_frames = 0;
    g.last_present_ns = 0;
    do_init();
}

void sleep_until(uint64_t target, uint64_t spin) {
    if (target > spin) {
        const uint64_t coarse = target - spin;
        timespec ts;
        ts.tv_sec = time_t(coarse / 1000000000ull);
        ts.tv_nsec = long(coarse % 1000000000ull);
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) == EINTR) {}
    }
    while (now_ns() < target) FP_CPU_RELAX();
}

// Chamada a cada present/swap (uma vez por frame, graças ao guard de profundidade).
void pace(uint32_t api) {
    Pacer& g = pacer();
    std::call_once(g.once, init_once);
    reinit_if_unlinked(now_ns());
    if (!g.shm) return;
    if (!(g.shm->api_mask & api)) __atomic_fetch_or(&g.shm->api_mask, api, __ATOMIC_RELAXED);

    const uint64_t now = now_ns();
    // Frame time real: intervalo desde o present anterior (antes da espera — o que o app viu).
    if (g.last_present_ns != 0) {
        uint64_t dt = (now - g.last_present_ns) / 1000ull;
        if (dt > 65535) dt = 65535;
        const uint32_t h = g.shm->ft_head.load(std::memory_order_relaxed);
        g.shm->ft[h % FT_N].store((uint16_t)dt, std::memory_order_relaxed);
        g.shm->ft_head.store(h + 1, std::memory_order_relaxed);
        if (g.shm->ft_count < FT_N) g.shm->ft_count++;
    }
    g.last_present_ns = now;
    const int32_t fps = g.shm->limit.load(std::memory_order_relaxed);
    uint64_t wait_until = 0;

    {
        std::lock_guard<std::mutex> lk(g.mu);

        // FPS medido, publicado a cada 500 ms
        if (g.win_start == 0) g.win_start = now;
        g.win_frames++;
        const uint64_t dt = now - g.win_start;
        if (dt >= 500000000ull) {
            const uint64_t x100 = uint64_t(g.win_frames) * 100ull * 1000000000ull / dt;
            g.shm->fps_x100.store(uint32_t(x100), std::memory_order_relaxed);
            g.shm->heartbeat_ns.store(now, std::memory_order_relaxed);
            g.win_frames = 0;
            g.win_start = now;
        }

        // Ritmo: reserva o próximo "slot" de apresentação
        if (fps > 0) {
            const uint64_t interval = 1000000000ull / uint64_t(fps);
            if (g.next_ns == 0 || now > g.next_ns + interval) g.next_ns = now;  // atrasou >1 frame: ressincroniza
            else if (g.next_ns > now + interval) g.next_ns = now + interval;    // limite foi aumentado
            if (g.next_ns > now) wait_until = g.next_ns;
            g.next_ns += interval;
        } else {
            g.next_ns = 0;
        }
    }

    if (wait_until) sleep_until(wait_until, g.spin_ns);
}

// Pace só no present/swap mais externo da thread; os internos passam direto.
struct Scope {
    explicit Scope(uint32_t api) { if (tl_depth++ == 0) pace(api); }
    ~Scope() { --tl_depth; }
};

// ---------------------------------------------------------------------------
// Interposição de OpenGL (GLX/EGL) — só tem efeito quando carregada via LD_PRELOAD
// ---------------------------------------------------------------------------
using DlsymFn = void* (*)(void*, const char*);

DlsymFn real_dlsym() {
    static DlsymFn fn = nullptr;
    if (!fn) {
        // dlsym tem versões de símbolo diferentes por arquitetura/glibc
        for (const char* v : {"GLIBC_2.2.5", "GLIBC_2.34", "GLIBC_2.17", "GLIBC_2.0"}) {
            fn = (DlsymFn)dlvsym(RTLD_NEXT, "dlsym", v);
            if (fn) break;
        }
    }
    return fn;
}

void* r_glXSwapBuffers = nullptr;
void* r_glXGetProcAddress = nullptr;
void* r_glXGetProcAddressARB = nullptr;
void* r_eglSwapBuffers = nullptr;
void* r_eglSwapBuffersWithDamageKHR = nullptr;
void* r_eglSwapBuffersWithDamageEXT = nullptr;
void* r_eglGetProcAddress = nullptr;

struct Entry { const char* name; void* hook; void** real; };
const Entry kEntries[] = {
    {"glXSwapBuffers", (void*)glXSwapBuffers, &r_glXSwapBuffers},
    {"glXGetProcAddress", (void*)glXGetProcAddress, &r_glXGetProcAddress},
    {"glXGetProcAddressARB", (void*)glXGetProcAddressARB, &r_glXGetProcAddressARB},
    {"eglSwapBuffers", (void*)eglSwapBuffers, &r_eglSwapBuffers},
    {"eglSwapBuffersWithDamageKHR", (void*)eglSwapBuffersWithDamageKHR, &r_eglSwapBuffersWithDamageKHR},
    {"eglSwapBuffersWithDamageEXT", (void*)eglSwapBuffersWithDamageEXT, &r_eglSwapBuffersWithDamageEXT},
    {"eglGetProcAddress", (void*)eglGetProcAddress, &r_eglGetProcAddress},
};

// Se `name` é uma função que hookeamos: guarda o ponteiro real e devolve o nosso hook.
void* intercept(const char* name, void* real) {
    if (!real || !name) return real;
    if (name[0] != 'g' && name[0] != 'e') return real;  // caminho rápido: só glX*/egl*
    for (const Entry& e : kEntries) {
        if (!strcmp(name, e.name)) {
            if (real != e.hook) *e.real = real;
            return e.hook;
        }
    }
    return real;
}

void* get_real(void** slot, const char* name) {
    if (void* p = *slot) return p;
    DlsymFn rd = real_dlsym();
    void* p = rd ? rd(RTLD_NEXT, name) : nullptr;
    if (p) *slot = p;
    return p;
}

// ---------------------------------------------------------------------------
// Boilerplate de camada Vulkan
// ---------------------------------------------------------------------------
struct InstanceData {
    VkInstance instance = VK_NULL_HANDLE;
    PFN_vkGetInstanceProcAddr gipa = nullptr;
    PFN_vkDestroyInstance DestroyInstance = nullptr;
};
struct DeviceData {
    PFN_vkGetDeviceProcAddr gdpa = nullptr;
    PFN_vkDestroyDevice DestroyDevice = nullptr;
    PFN_vkQueuePresentKHR QueuePresentKHR = nullptr;
};

std::mutex g_map_mu;
std::unordered_map<void*, InstanceData> g_instances;
std::unordered_map<void*, DeviceData> g_devices;

// Objetos despachaveis do Vulkan começam com o ponteiro da tabela de despacho.
inline void* dispatch_key(const void* handle) { return *reinterpret_cast<void* const*>(handle); }

VKAPI_ATTR VkResult VKAPI_CALL fp_CreateInstance(const VkInstanceCreateInfo* ci,
                                                 const VkAllocationCallbacks* alloc,
                                                 VkInstance* out) {
    auto* chain = (VkLayerInstanceCreateInfo*)ci->pNext;
    while (chain && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO &&
                      chain->function == VK_LAYER_LINK_INFO))
        chain = (VkLayerInstanceCreateInfo*)chain->pNext;
    if (!chain) return VK_ERROR_INITIALIZATION_FAILED;

    PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;

    auto create = (PFN_vkCreateInstance)gipa(VK_NULL_HANDLE, "vkCreateInstance");
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = create(ci, alloc, out);
    if (r != VK_SUCCESS) return r;

    InstanceData d;
    d.instance = *out;
    d.gipa = gipa;
    d.DestroyInstance = (PFN_vkDestroyInstance)gipa(*out, "vkDestroyInstance");
    std::lock_guard<std::mutex> lk(g_map_mu);
    g_instances[dispatch_key(*out)] = d;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL fp_DestroyInstance(VkInstance instance, const VkAllocationCallbacks* alloc) {
    InstanceData d;
    {
        std::lock_guard<std::mutex> lk(g_map_mu);
        auto it = g_instances.find(dispatch_key(instance));
        if (it == g_instances.end()) return;
        d = it->second;
        g_instances.erase(it);
    }
    d.DestroyInstance(instance, alloc);
}

VKAPI_ATTR VkResult VKAPI_CALL fp_CreateDevice(VkPhysicalDevice phys, const VkDeviceCreateInfo* ci,
                                               const VkAllocationCallbacks* alloc, VkDevice* out) {
    auto* chain = (VkLayerDeviceCreateInfo*)ci->pNext;
    while (chain && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO &&
                      chain->function == VK_LAYER_LINK_INFO))
        chain = (VkLayerDeviceCreateInfo*)chain->pNext;
    if (!chain) return VK_ERROR_INITIALIZATION_FAILED;

    VkInstance instance = VK_NULL_HANDLE;
    {
        std::lock_guard<std::mutex> lk(g_map_mu);
        auto it = g_instances.find(dispatch_key(phys));
        if (it != g_instances.end()) instance = it->second.instance;
    }

    PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr gdpa = chain->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;

    auto create = (PFN_vkCreateDevice)gipa(instance, "vkCreateDevice");
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = create(phys, ci, alloc, out);
    if (r != VK_SUCCESS) return r;

    DeviceData d;
    d.gdpa = gdpa;
    d.DestroyDevice = (PFN_vkDestroyDevice)gdpa(*out, "vkDestroyDevice");
    d.QueuePresentKHR = (PFN_vkQueuePresentKHR)gdpa(*out, "vkQueuePresentKHR");
    std::lock_guard<std::mutex> lk(g_map_mu);
    g_devices[dispatch_key(*out)] = d;
    return VK_SUCCESS;
}

VKAPI_ATTR void VKAPI_CALL fp_DestroyDevice(VkDevice device, const VkAllocationCallbacks* alloc) {
    DeviceData d;
    {
        std::lock_guard<std::mutex> lk(g_map_mu);
        auto it = g_devices.find(dispatch_key(device));
        if (it == g_devices.end()) return;
        d = it->second;
        g_devices.erase(it);
    }
    d.DestroyDevice(device, alloc);
}

VKAPI_ATTR VkResult VKAPI_CALL fp_QueuePresentKHR(VkQueue queue, const VkPresentInfoKHR* info) {
    PFN_vkQueuePresentKHR next = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_map_mu);
        auto it = g_devices.find(dispatch_key(queue));
        if (it != g_devices.end()) next = it->second.QueuePresentKHR;
    }
    if (!next) return VK_ERROR_INITIALIZATION_FAILED;
    Scope scope(API_VULKAN);  // espera ANTES de apresentar => apresentações igualmente espaçadas
    return next(queue, info);
}

#define FP_HOOK(fn) if (!strcmp(name, "vk" #fn)) return (PFN_vkVoidFunction)fp_##fn

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL fp_GetDeviceProcAddr(VkDevice device, const char* name) {
    FP_HOOK(GetDeviceProcAddr);
    FP_HOOK(DestroyDevice);
    DeviceData d;
    {
        std::lock_guard<std::mutex> lk(g_map_mu);
        auto it = g_devices.find(dispatch_key(device));
        if (it == g_devices.end()) return nullptr;
        d = it->second;
    }
    if (!strcmp(name, "vkQueuePresentKHR") && d.QueuePresentKHR)
        return (PFN_vkVoidFunction)fp_QueuePresentKHR;
    return d.gdpa ? d.gdpa(device, name) : nullptr;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL fp_GetInstanceProcAddr(VkInstance instance, const char* name) {
    FP_HOOK(GetInstanceProcAddr);
    FP_HOOK(CreateInstance);
    FP_HOOK(DestroyInstance);
    FP_HOOK(CreateDevice);
    FP_HOOK(GetDeviceProcAddr);
    if (!instance) return nullptr;
    PFN_vkGetInstanceProcAddr next = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_map_mu);
        auto it = g_instances.find(dispatch_key(instance));
        if (it != g_instances.end()) next = it->second.gipa;
    }
    return next ? next(instance, name) : nullptr;
}

}  // namespace

// ---------------------------------------------------------------------------
// Exports: Vulkan
// ---------------------------------------------------------------------------
FP_EXPORT VKAPI_ATTR VkResult VKAPI_CALL
vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface* v) {
    if (!v || v->sType != LAYER_NEGOTIATE_INTERFACE_STRUCT) return VK_ERROR_INITIALIZATION_FAILED;
    if (v->loaderLayerInterfaceVersion >= 2) {
        v->loaderLayerInterfaceVersion = 2;
        v->pfnGetInstanceProcAddr = fp_GetInstanceProcAddr;
        v->pfnGetDeviceProcAddr = fp_GetDeviceProcAddr;
        v->pfnGetPhysicalDeviceProcAddr = nullptr;
    }
    return VK_SUCCESS;
}

// ---------------------------------------------------------------------------
// Exports: OpenGL (GLX / EGL) e dlsym
// ---------------------------------------------------------------------------
void glXSwapBuffers(void* dpy, unsigned long drawable) {
    auto fn = (void (*)(void*, unsigned long))get_real(&r_glXSwapBuffers, "glXSwapBuffers");
    if (!fn) return;
    Scope scope(API_GLX);
    fn(dpy, drawable);
}

unsigned int eglSwapBuffers(void* dpy, void* surface) {
    auto fn = (unsigned int (*)(void*, void*))get_real(&r_eglSwapBuffers, "eglSwapBuffers");
    if (!fn) return 0;
    Scope scope(API_EGL);
    return fn(dpy, surface);
}

unsigned int eglSwapBuffersWithDamageKHR(void* dpy, void* surface, const int* rects, int n) {
    auto fn = (unsigned int (*)(void*, void*, const int*, int))
        get_real(&r_eglSwapBuffersWithDamageKHR, "eglSwapBuffersWithDamageKHR");
    if (!fn) return 0;
    Scope scope(API_EGL);
    return fn(dpy, surface, rects, n);
}

unsigned int eglSwapBuffersWithDamageEXT(void* dpy, void* surface, const int* rects, int n) {
    auto fn = (unsigned int (*)(void*, void*, const int*, int))
        get_real(&r_eglSwapBuffersWithDamageEXT, "eglSwapBuffersWithDamageEXT");
    if (!fn) return 0;
    Scope scope(API_EGL);
    return fn(dpy, surface, rects, n);
}

void* glXGetProcAddress(const unsigned char* name) {
    auto fn = (void* (*)(const unsigned char*))get_real(&r_glXGetProcAddress, "glXGetProcAddress");
    return intercept((const char*)name, fn ? fn(name) : nullptr);
}

void* glXGetProcAddressARB(const unsigned char* name) {
    auto fn = (void* (*)(const unsigned char*))get_real(&r_glXGetProcAddressARB, "glXGetProcAddressARB");
    return intercept((const char*)name, fn ? fn(name) : nullptr);
}

void* eglGetProcAddress(const char* name) {
    auto fn = (void* (*)(const char*))get_real(&r_eglGetProcAddress, "eglGetProcAddress");
    return intercept(name, fn ? fn(name) : nullptr);
}

// GLFW/SDL/Wine carregam libGL/libEGL com dlopen + dlsym(handle, ...), o que
// escaparia da interposição normal. Por isso interceptamos o próprio dlsym.
// Guard de reentrância por thread evita loops com interpositores pré-existentes
// que usam dlsym durante sua inicialização (ex.: Steam Overlay / MangoHud).
thread_local bool tl_in_dlsym = false;

void* fp_dlsym(void* handle, const char* name) {
    DlsymFn rd = real_dlsym();
    if (!rd) return nullptr;
    if (tl_in_dlsym) return rd(handle, name);
    struct DlsymGuard {
        DlsymGuard() { tl_in_dlsym = true; }
        ~DlsymGuard() { tl_in_dlsym = false; }
    } guard;
    void* r = rd(handle, name);
    if (handle == RTLD_NEXT || handle == RTLD_DEFAULT) return r;
    return intercept(name, r);
}
