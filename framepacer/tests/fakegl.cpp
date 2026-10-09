// "libGL/libEGL" falsa: só conta quantos swaps chegaram de verdade ao driver.
#include <cstring>
extern "C" {
volatile int fake_swaps = 0;
void glXSwapBuffers(void*, unsigned long) { fake_swaps = fake_swaps + 1; }
unsigned eglSwapBuffersWithDamageKHR(void*, void*, const int*, int) { fake_swaps = fake_swaps + 1; return 1; }
void* eglGetProcAddress(const char* n) {
    if (!strcmp(n, "eglSwapBuffersWithDamageKHR")) return (void*)eglSwapBuffersWithDamageKHR;
    return nullptr;
}
}
