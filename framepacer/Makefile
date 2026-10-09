PREFIX   ?= /usr
LIBDIR   ?= $(PREFIX)/lib
LIBDIR32 ?= $(PREFIX)/lib32
VKLAYERDIR ?= $(PREFIX)/share/framepacer/vk-layer
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
	sed -e 's|@LIBDIR@|$(LIBDIR)|' -e 's|@LIBDIR32@|$(LIBDIR32)|' -e 's|@VKLAYERDIR@|$(VKLAYERDIR)|' framepacer-run.in > framepacer-run.tmp
	install -Dm755 framepacer-run.tmp $(DESTDIR)$(PREFIX)/bin/framepacer-run
	rm -f framepacer-run.tmp
	install -Dm644 framepacer.desktop $(DESTDIR)$(PREFIX)/share/applications/framepacer.desktop
	install -d $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d
	sed 's|@LIBPATH@|$(LIBDIR)/$(LIB)|' framepacer.json.in > $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d/framepacer.json
	chmod 644 $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d/framepacer.json
	install -d $(DESTDIR)$(VKLAYERDIR)
	sed 's|@LIBPATH@|$(LIBDIR)/$(LIB)|' framepacer.json.in > $(DESTDIR)$(VKLAYERDIR)/framepacer.json
	chmod 644 $(DESTDIR)$(VKLAYERDIR)/framepacer.json

install-lib32: lib32
	install -Dm755 build32/$(LIB) $(DESTDIR)$(LIBDIR32)/$(LIB)

uninstall:
	rm -f $(DESTDIR)$(LIBDIR)/$(LIB) $(DESTDIR)$(LIBDIR32)/$(LIB) \
	      $(DESTDIR)$(PREFIX)/bin/framepacer $(DESTDIR)$(PREFIX)/bin/framepacer-run \
	      $(DESTDIR)$(PREFIX)/share/applications/framepacer.desktop \
	      $(DESTDIR)$(PREFIX)/share/vulkan/implicit_layer.d/framepacer.json \
	      $(DESTDIR)$(VKLAYERDIR)/framepacer.json
	-rmdir $(DESTDIR)$(VKLAYERDIR) $(DESTDIR)$(PREFIX)/share/framepacer 2>/dev/null

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
	echo "== Diretorio padrao (state, sem FRAMEPACER_DIR) =="; \
	FRAMEPACER_DIR= XDG_STATE_HOME=/tmp/fp-state-$$$$ HOME=/tmp/fp-home-$$$$ XDG_RUNTIME_DIR=/tmp/fp-run-$$$$ \
	python3 -c "import sys; sys.path.insert(0, '.'); import framepacer as f; d = f.base_dir(); assert d == '/tmp/fp-state-$$$$/framepacer', d; print('base_dir OK:', d)"; \
	echo "== Dir aninhado inexistente (mkdir -p no C++) =="; \
	D2=/tmp/fp-deep-$$$$; rm -rf $$D2; \
	FRAMEPACER_DIR=$$D2/a/b ./build/test_layer ./build/$(LIB) ./framepacer.py > build/nested.log 2>&1; rc=$$?; tail -2 build/nested.log; rm -rf $$D2; test $$rc -eq 0 && echo "nested FRAMEPACER_DIR OK"; \
	echo "== Wrapper exporta VK_ADD_LAYER_PATH =="; \
	sed -e 's|@LIBDIR@|/nonexistent|' -e 's|@LIBDIR32@|/nonexistent32|' -e 's|@VKLAYERDIR@|/nonexistent-vk|' framepacer-run.in > build/framepacer-run.test; \
	sh -n build/framepacer-run.test && echo "wrapper syntax OK"; \
	rm -rf build/fakehome; mkdir -p build/fakehome/.local/share/framepacer/vk-layer && touch build/fakehome/.local/share/framepacer/vk-layer/framepacer.json; \
	HOME=build/fakehome env -u XDG_DATA_HOME sh build/framepacer-run.test env | grep -q "framepacer/vk-layer" && echo "wrapper VK_ADD_LAYER_PATH OK"; \
	echo; echo "TODOS OS TESTES PASSARAM"

clean:
	rm -rf build build32

.PHONY: all lib32 install install-lib32 uninstall test clean
