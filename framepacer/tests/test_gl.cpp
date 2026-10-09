// Testa os hooks OpenGL via LD_PRELOAD contra uma libGL falsa.
//  - modo dl:     dlopen(RTLD_LOCAL)+dlsym(handle) como GLFW/SDL/Wine (exige o hook de dlsym)
//  - modo link:   chamada direta resolvida por interposição
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#ifdef LINKED
extern "C" void glXSwapBuffers(void*, unsigned long);
#endif
static double now_s() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static volatile int* swaps; static int failures = 0; static const char* script;

template <class F> static void phase(const char* tag, int limit, int frames, double lo, double hi, F call) {
    char cmd[512]; snprintf(cmd, sizeof cmd, "env -u LD_PRELOAD python3 %s --set %d > /dev/null", script, limit);
    if (system(cmd) != 0) { printf("FALHOU: CLI nao achou o jogo\n"); failures++; }
    int before = *swaps; double t0 = now_s();
    for (int i = 0; i < frames; i++) call();
    double fps = frames / (now_s() - t0); int got = *swaps - before;
    bool ok = fps >= lo && fps <= hi && got == frames; if (!ok) failures++;
    printf("[%s] %-14s limite=%-3d -> %.1f FPS (esperado %.0f..%.0f), swaps reais %d/%d\n", ok ? "OK" : "FALHOU", tag, limit, fps, lo, hi, got, frames);
}
int main(int, char** argv) {
    script = argv[2];
    void* h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!h) { printf("dlopen: %s\n", dlerror()); return 2; }
    swaps = (volatile int*)dlsym(h, "fake_swaps");
#ifdef LINKED
    auto glx = [] { glXSwapBuffers(nullptr, 0); };
    glx();
    phase("glX (linkado)", 60, 90, 57, 63, glx);
    phase("glX (linkado)", 144, 216, 137, 151, glx);
    phase("glX (linkado)", 0, 100000, 20000, 1e12, glx);
#else
    auto fglx = (void (*)(void*, unsigned long))dlsym(h, "glXSwapBuffers");
    auto gpa = (void* (*)(const char*))dlsym(h, "eglGetProcAddress");
    auto fegl = (unsigned (*)(void*, void*, const int*, int))gpa("eglSwapBuffersWithDamageKHR");
    if (!fglx || !fegl) { printf("FALHOU: simbolos nao resolvidos\n"); return 2; }
    auto glx = [&] { fglx(nullptr, 0); };
    auto egl = [&] { fegl(nullptr, nullptr, nullptr, 0); };
    glx();
    phase("glX (dlsym)", 60, 90, 57, 63, glx);
    phase("glX (dlsym)", 144, 216, 137, 151, glx);
    phase("EGL (gpa)", 60, 90, 57, 63, egl);
    phase("EGL (gpa)", 0, 100000, 20000, 1e12, egl);
#endif
    return failures ? 1 : 0;
}
