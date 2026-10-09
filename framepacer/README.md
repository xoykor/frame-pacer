# FramePacer

Limitador/ritmador de FPS **universal** para Linux: Vulkan (DXVK, VKD3D-Proton, nativos) e
OpenGL (GLX/EGL: Minecraft Java, jogos nativos, Wine/WineD3D, SDL/GLFW), com GUI Qt6.
O limite muda **em tempo real**, sem reiniciar o jogo. Independe do compositor (KDE Wayland ou X11).

## Dependências (CachyOS/Arch)
    sudo pacman -S --needed base-devel vulkan-headers pyside6

## Instalar
    make
    sudo make install            # recomendado (visível ao runtime do Steam)
    # ou, sem root:  make install PREFIX=$HOME/.local
    # jogos 32 bits (opcional, precisa de multilib):  sudo make install-lib32

## Usar
- Qualquer programa:  `framepacer-run ./jogo`   (Prism Launcher: Comando de encapsulamento = `framepacer-run`)
- Steam, Opções de inicialização:  `framepacer-run %command%`
  (jogos Proton só com DX9-12 também funcionam com `ENABLE_FRAMEPACER=1 %command%`, via camada Vulkan)
- Abra o **FramePacer** pelo menu de aplicativos (ou `framepacer`). Atalhos do KDE
  (Configurações > Atalhos > Adicionar comando): `framepacer --set 60`, `framepacer --set 0`.

## Cobertura
| Caminho de apresentação | Coberto |
|---|---|
| vkQueuePresentKHR (Vulkan, DXVK, VKD3D) | sim — camada Vulkan |
| glXSwapBuffers (OpenGL no X11/Xwayland) | sim — LD_PRELOAD |
| eglSwapBuffers / WithDamage (OpenGL no Wayland nativo) | sim — LD_PRELOAD |
| Zink, Wine OpenGL, SDL, GLFW (dlopen+dlsym) | sim — hook de dlsym |
| XPutImage / renderização por software / vídeo | não |

## Variáveis de ambiente do jogo
- `FRAMEPACER_FPS=N`      limite inicial (senão usa o padrão salvo na GUI)
- `FRAMEPACER_SPIN_US=N`  espera ativa final em µs (padrão 200; 0 = só dormir)
- `FRAMEPACER_DIR=...`    onde ficam os arquivos de controle (padrão `$XDG_RUNTIME_DIR/framepacer`)
- `DISABLE_FRAMEPACER=1`  desativa tudo

## Limitações conhecidas
- Não combine com outros interpositores no LD_PRELOAD (ex.: MangoHud em modo GL); a camada Vulkan convive bem.
- Jogo já aberto sem `framepacer-run`/`ENABLE_FRAMEPACER=1` precisa de um restart para entrar no esquema.
- Com VSync e sem VRR o limite fica quantizado ao refresh do monitor.

## Testes (sem GPU)
    make test      # Vulkan (loader simulado) + GLX/EGL (libGL falsa), mudando o limite em tempo real
    # se o vulkan-headers não estiver em /usr/include:  make test VKINC=-I/caminho/Vulkan-Headers/include
