# FramePacer

Limitador e ritmador de FPS **universal** para Linux: Vulkan (DXVK, VKD3D-Proton, nativos) e OpenGL (GLX/EGL — Minecraft Java, jogos nativos, Wine/WineD3D, SDL/GLFW), com GUI em Qt6.

O limite muda **em tempo real**, sem reiniciar o jogo. Funciona no X11 e no Wayland (KDE incluído), independente do compositor.

Distribuição: **pacote nativo** (veja [Instalação](#instalação)). AppImage/Flatpak foram avaliados e descartados como formato principal: o injetor precisa de `LD_PRELOAD` + camada Vulkan implícita + `mmap` compartilhado com o jogo no host, o que quebra dentro de sandbox/mount efêmero.

## Como funciona

* Uma única biblioteca `libframepacer.so` faz os dois papéis:
  * **Vulkan:** camada implícita que cerca `vkQueuePresentKHR`.
  * **OpenGL:** hooks de `glXSwapBuffers` / `eglSwapBuffers` (incluindo `WithDamage`) via `LD_PRELOAD`, com interceptação de `dlsym` para cobrir GLFW/SDL/Wine que resolvem os símbolos com `dlopen`.
* Um guard por thread evita pacing duplo (ex.: Zink, que chama `vkQueuePresentKHR` de dentro de `eglSwapBuffers`).
* Cada jogo publica um arquivo de controle de 64 bytes em `$XDG_RUNTIME_DIR/framepacer/<pid>.shm` (limite, FPS medido, heartbeat). A GUI e a CLI alteram o limite lendo/escrevendo esse arquivo — sem reiniciar nada.
* O wrapper `framepacer-run` injeta a biblioteca (`LD_PRELOAD` + `ENABLE_FRAMEPACER=1`) em qualquer programa.

## Requisitos

* Linux x86_64, `g++`, `make`
* Headers do Vulkan: `vulkan-headers` (Arch/CachyOS: `base-devel vulkan-headers`)
* GUI/CLI: `python3` + `pyside6`

```sh
sudo pacman -S --needed base-devel vulkan-headers pyside6
```

## Instalação

```sh
make
sudo make install            # recomendado: fica visível para o runtime do Steam
# sem root:
make install PREFIX=$HOME/.local
# jogos 32 bits (opcional, precisa de multilib):
sudo make install-lib32
```

## Jogos Proton (Satisfactory, Detroit, ...)

O container do Proton (pressure-vessel) esconde `/usr` e `/run` do jogo, então além da instalação normal faça também a instalação user-level (sem sudo) — ela põe a biblioteca e o manifesto da camada em `~/.local`, que o container enxerga:

```sh
make install PREFIX=$HOME/.local
```

A opção de inicialização continua `framepacer-run %command%`. Reinicie o jogo após atualizar.

O que `make install` coloca no sistema (veja `framepacer/Makefile`):

| Artefato | Destino |
|---|---|
| `libframepacer.so` | `$LIBDIR/libframepacer.so` |
| `framepacer` (GUI + CLI, Qt6) | `$PREFIX/bin/framepacer` |
| `framepacer-run` (wrapper) | `$PREFIX/bin/framepacer-run` |
| `framepacer.desktop` | `$PREFIX/share/applications/framepacer.desktop` |
| `framepacer.json` (camada Vulkan implícita) | `$PREFIX/share/vulkan/implicit_layer.d/framepacer.json` |

Desinstalar: `sudo make uninstall`.

## Uso

Qualquer programa:

```sh
framepacer-run ./jogo
```

Prism Launcher: comando de encapsulamento = `framepacer-run`.

Steam → Opções de inicialização:

```
framepacer-run %command%
```

Jogos Proton que só usam DX9–12 também funcionam só com a camada Vulkan:

```
ENABLE_FRAMEPACER=1 %command%
```

Abra o **FramePacer** pelo menu de aplicativos (ou `framepacer`) para ajustar o limite com sliders, presets e leitura de FPS. Cada jogo mostra gráfico de frame times e botão ⏻ (liga/desliga o limite; desligado = sem limite); o botão **Overlay FPS** exibe um contador 12px sobre o jogo. Fechar a janela (X) minimiza para a bandeja; para encerrar use **Sair** no menu do ícone. Atalhos úteis no KDE (Configurações → Atalhos → Adicionar comando):

```
framepacer --set 60
framepacer --set 0
```

### CLI

```
framepacer --list              # lista jogos com a camada ativa, API, limite e FPS
framepacer --set 60            # todos os jogos abertos
framepacer --set 0 --pid 1234 # sem limite, só um processo
framepacer --default 60        # limite inicial dos próximos jogos
framepacer --gui               # abre a interface gráfica
```

### Variáveis de ambiente do jogo

| Variável | Efeito |
|---|---|
| `FRAMEPACER_FPS=N` | limite inicial (senão usa o padrão salvo na GUI) |
| `FRAMEPACER_SPIN_US=N` | espera ativa final em µs, padrão `200` (`0` = só dormir) |
| `FRAMEPACER_DIR=...` | onde ficam os arquivos de controle (padrão `~/.local/state/framepacer`) |
| `DISABLE_FRAMEPACER=1` | desativa tudo |
| `ENABLE_FRAMEPACER=1` | ativa a camada Vulkan implícita |

## Cobertura

| Caminho de apresentação | Coberto |
|---|---|
| `vkQueuePresentKHR` (Vulkan, DXVK, VKD3D) | sim — camada Vulkan |
| `glXSwapBuffers` (OpenGL no X11/Xwayland) | sim — `LD_PRELOAD` |
| `eglSwapBuffers` / `WithDamage` (OpenGL no Wayland nativo) | sim — `LD_PRELOAD` |
| Zink, Wine OpenGL, SDL, GLFW (`dlopen`+`dlsym`) | sim — hook de `dlsym` |
| `XPutImage` / renderização por software / vídeo | não |

## Limitações conhecidas

* Não combine com outros interpositores no `LD_PRELOAD` (ex.: MangoHud em modo GL); a camada Vulkan convive bem.
* Jogo já aberto sem `framepacer-run` / `ENABLE_FRAMEPACER=1` precisa de restart para entrar no esquema.
* Com VSync e sem VRR o limite fica quantizado ao refresh do monitor.

## Testes (sem GPU)

```sh
make test      # loader Vulkan simulado + libGL/libEGL falsas, mudando o limite em tempo real
# se o vulkan-headers não estiver em /usr/include:
make test VKINC=-I/caminho/Vulkan-Headers/include
```

Cobre: camada Vulkan (`tests/test_layer.cpp`), GLX/EGL via `dlopen`+`dlsym` e via linkagem direta (`tests/test_gl.cpp`, `tests/fakegl.cpp`).

## Estrutura

```
framepacer/
  framepacer.cpp      # libframepacer.so: camada Vulkan + hooks GLX/EGL + pacing
  framepacer.py       # GUI Qt6 + CLI (mmap do .shm, presets, --set/--list/--default)
  framepacer-run.in   # template do wrapper (LD_PRELOAD + ENABLE_FRAMEPACER)
  framepacer.json.in  # template da camada Vulkan implícita
  framepacer.desktop  # entrada do menu de aplicativos
  Makefile            # build, install/uninstall, lib32, test
  tests/              # harness sem GPU
```

## Licença

MIT — veja `SPDX-License-Identifier: MIT` nos fontes.
