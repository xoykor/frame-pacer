PREFIX   ?= /usr
LIBDIR   ?= $(PREFIX)/lib
LIBDIR32 ?= $(PREFIX)/lib32
DESTDIR  ?=
CXX      ?= g++
LIB      := libframepacer.so
CXXFLAGS ?= -O2
CXXFLAGS += -std=c++17 -fPIC -fvisibility=hidden -Wall -Wextra
VKINC    ?=

all: build/$(LIB)

build/$(LIB): framepacer.cpp
	@mkdir -p build
	$(CXX) $(CXXFLAGS) $(VKINC) -shared $< -o $@ -ldl -lpthread

# Opcional: versão 32 bits para jogos 32-bit (precisa de gcc-multilib / lib32-gcc-libs)
lib32: framepacer.cpp
	@mkdir -p build32
	$(CXX) -m32 $(CXXFLAGS) $(VKINC) -shared $< -o build32/$(LIB) -ldl -lpthread

install: all
	install -Dm755 build/$(LIB) $(DESTDIR)$(LIBDIR)/$(LIB)
	install -Dm755 framepacer.py $(DESTDIR)$(PREFIX)/bin/framepacer
	sed -e 's|@LIBDIR@|$(LIBDIR)|' -e 's|@LIBDIR32@|$(LIBDIR32)|' framepacer-run.in > framepacer-run.tmp
	install -Dm755 framepacer-run.tmp $(DESTDIR)$(PREFIX)/bin/framepacer-run
	rm -f framepacer-run.tmp
	install -Dm644 framepacer.desktop $(DESTDIR)$(PREFIX)/share/applications/framepacer.desktop
	install -d $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d
	sed 's|@LIBPATH@|$(LIBDIR)/$(LIB)|' framepacer.json.in > $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d/framepacer.json
	chmod 644 $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d/framepacer.json

install-lib32: lib32
	install -Dm755 build32/$(LIB) $(DESTDIR)$(LIBDIR32)/$(LIB)

uninstall:
	rm -f $(DESTDIR)$(LIBDIR)/$(LIB) $(DESTDIR)$(LIBDIR32)/$(LIB) \
	      $(DESTDIR)$(PREFIX)/bin/framepacer $(DESTDIR)$(PREFIX)/bin/framepacer-run \
	      $(DESTDIR)$(PREFIX)/share/applications/framepacer.desktop \
	      $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d/framepacer.json

# Testes sem GPU: loader Vulkan simulado + libGL/libEGL falsas (Vulkan, GLX, EGL)
test: all
	$(CXX) -std=c++17 -O2 $(VKINC) tests/test_layer.cpp -o build/test_layer -ldl
	$(CXX) -shared -fPIC -Wl,-Bsymbolic tests/fakegl.cpp -o build/libfakegl.so
	$(CXX) -O2 tests/test_gl.cpp -o build/test_gl_dl -ldl
	$(CXX) -O2 -DLINKED tests/test_gl.cpp -o build/test_gl_link -ldl -Lbuild -lfakegl -Wl,-rpath,'$$ORIGIN'
	@D=$$(mktemp -d); set -e; \
	echo "== Vulkan (camada) =="; FRAMEPACER_DIR=$$D ./build/test_layer ./build/$(LIB) ./framepacer.py; \
	echo "== OpenGL via dlopen/dlsym (GLFW/SDL/Wine) =="; FRAMEPACER_DIR=$$D LD_PRELOAD=./build/$(LIB) ./build/test_gl_dl ./build/libfakegl.so ./framepacer.py; \
	echo "== OpenGL linkado direto =="; FRAMEPACER_DIR=$$D LD_PRELOAD=./build/$(LIB) ./build/test_gl_link ./build/libfakegl.so ./framepacer.py; \
	echo; echo "TODOS OS TESTES PASSARAM"

clean:
	rm -rf build build32

.PHONY: all lib32 install install-lib32 uninstall test clean
