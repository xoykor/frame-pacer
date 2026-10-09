// Simula o loader Vulkan (cadeia de camadas falsa) para testar a camada sem GPU.
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>
#include <dlfcn.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>

typedef VkResult (VKAPI_PTR *PFN_negotiate)(VkNegotiateLayerInterface*);

static int present_calls = 0;
static void* inst_obj[4]; static void* phys_obj[4]; static void* dev_obj[4]; static void* queue_obj[4];

static VkResult VKAPI_CALL f_present(VkQueue, const VkPresentInfoKHR*) { present_calls++; return VK_SUCCESS; }
static void VKAPI_CALL f_destroy_dev(VkDevice, const VkAllocationCallbacks*) {}
static void VKAPI_CALL f_destroy_inst(VkInstance, const VkAllocationCallbacks*) {}
static VkResult VKAPI_CALL f_create_inst(const VkInstanceCreateInfo*, const VkAllocationCallbacks*, VkInstance* o) { *o = (VkInstance)inst_obj; return VK_SUCCESS; }
static VkResult VKAPI_CALL f_create_dev(VkPhysicalDevice, const VkDeviceCreateInfo*, const VkAllocationCallbacks*, VkDevice* o) { *o = (VkDevice)dev_obj; return VK_SUCCESS; }
static PFN_vkVoidFunction VKAPI_CALL f_gipa(VkInstance, const char* n) {
    if (!strcmp(n, "vkCreateInstance")) return (PFN_vkVoidFunction)f_create_inst;
    if (!strcmp(n, "vkDestroyInstance")) return (PFN_vkVoidFunction)f_destroy_inst;
    if (!strcmp(n, "vkCreateDevice")) return (PFN_vkVoidFunction)f_create_dev;
    return nullptr;
}
static PFN_vkVoidFunction VKAPI_CALL f_gdpa(VkDevice, const char* n) {
    if (!strcmp(n, "vkQueuePresentKHR")) return (PFN_vkVoidFunction)f_present;
    if (!strcmp(n, "vkDestroyDevice")) return (PFN_vkVoidFunction)f_destroy_dev;
    return nullptr;
}
static double now_s() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

static PFN_vkQueuePresentKHR present;
static int failures = 0;
static void phase(const char* script, int limit, int frames, double lo, double hi) {
    char cmd[512]; snprintf(cmd, sizeof cmd, "python3 %s --set %d > /dev/null", script, limit);
    if (system(cmd) != 0) { printf("FAIL: CLI nao achou o jogo\n"); failures++; }
    double t0 = now_s();
    for (int i = 0; i < frames; i++) present((VkQueue)queue_obj, nullptr);
    double fps = frames / (now_s() - t0);
    bool ok = fps >= lo && fps <= hi; if (!ok) failures++;
    printf("[%s] limite=%-3d -> medido %.1f FPS (esperado %.0f..%.0f)\n", ok ? "OK" : "FALHOU", limit, fps, lo, hi);
}

int main(int, char** argv) {
    inst_obj[0] = (void*)&inst_obj; dev_obj[0] = (void*)&dev_obj;
    phys_obj[0] = inst_obj[0]; queue_obj[0] = dev_obj[0];

    void* h = dlopen(argv[1], RTLD_NOW);
    if (!h) { printf("dlopen: %s\n", dlerror()); return 2; }
    auto neg = (PFN_negotiate)dlsym(h, "vkNegotiateLoaderLayerInterfaceVersion");
    VkNegotiateLayerInterface ni{}; ni.sType = LAYER_NEGOTIATE_INTERFACE_STRUCT; ni.loaderLayerInterfaceVersion = 2;
    if (neg(&ni) != VK_SUCCESS) return 2;

    VkLayerInstanceLink il{nullptr, f_gipa, nullptr};
    VkLayerInstanceCreateInfo lci{}; lci.sType = VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO; lci.function = VK_LAYER_LINK_INFO; lci.u.pLayerInfo = &il;
    VkInstanceCreateInfo ci{}; ci.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO; ci.pNext = &lci;
    auto create_inst = (PFN_vkCreateInstance)ni.pfnGetInstanceProcAddr(VK_NULL_HANDLE, "vkCreateInstance");
    VkInstance inst; if (create_inst(&ci, nullptr, &inst) != VK_SUCCESS) return 2;

    VkLayerDeviceLink dl{nullptr, f_gipa, f_gdpa};
    VkLayerDeviceCreateInfo dci{}; dci.sType = VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO; dci.function = VK_LAYER_LINK_INFO; dci.u.pLayerInfo = &dl;
    VkDeviceCreateInfo dc{}; dc.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO; dc.pNext = &dci;
    auto create_dev = (PFN_vkCreateDevice)ni.pfnGetInstanceProcAddr(inst, "vkCreateDevice");
    VkDevice dev; if (create_dev((VkPhysicalDevice)phys_obj, &dc, nullptr, &dev) != VK_SUCCESS) return 2;
    present = (PFN_vkQueuePresentKHR)ni.pfnGetDeviceProcAddr(dev, "vkQueuePresentKHR");

    present((VkQueue)queue_obj, nullptr);  // 1o frame cria o .shm
    const char* script = argv[2];
    phase(script, 60, 120, 57, 63);          // ~2 s
    phase(script, 144, 288, 137, 151);       // alterado em tempo real (~2 s)
    phase(script, 30, 45, 28.5, 31.5);       // reduzido em tempo real (~1,5 s)
    phase(script, 0, 200000, 20000, 1e12);   // sem limite
    char cmd[512]; snprintf(cmd, sizeof cmd, "python3 %s --list", script); (void)!system(cmd);
    {  // ring de frame times (protocolo v2): tamanho, versão, head/count
        const char* dir = getenv("FRAMEPACER_DIR");
        char p[512];
        snprintf(p, sizeof p, "%s/%d.shm", dir && *dir ? dir : "/tmp", (int)getpid());
        uint8_t buf[80] = {0};
        FILE* f = fopen(p, "rb");
        size_t n = f ? fread(buf, 1, sizeof buf, f) : 0;
        if (f) fclose(f);
        uint32_t magic = 0, version = 0, head = 0, count = 0;
        long sz = -1;
        if (n >= 76) {
            struct stat st;
            if (stat(p, &st) == 0) sz = (long)st.st_size;
            memcpy(&magic, buf, 4);
            memcpy(&version, buf + 4, 4);
            memcpy(&head, buf + 64, 4);
            memcpy(&count, buf + 68, 4);
        }
        bool ok = n >= 76 && sz >= 320 && magic == 0x43415046 && version == 2 &&
                  head > 0 && count > 0 && count <= 120;
        printf("[%s] ring frame-times: size=%ld head=%u count=%u\n", ok ? "OK" : "FALHOU", sz, head, count);
        if (!ok) failures++;
    }
    printf(failures ? "\nRESULTADO: FALHOU\n" : "\nRESULTADO: tudo OK (%d presents)\n", present_calls);
    return failures ? 1 : 0;
}
