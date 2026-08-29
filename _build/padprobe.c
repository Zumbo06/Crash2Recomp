/* Gamepad probe.
 *
 * Links against the exact SDL3 the recompiled game uses, and reports what the
 * joystick/gamepad layer can actually see. Keeps the question "is the pad
 * visible to SDL?" separate from "does the game respond to it?".
 *
 * Run it, then hotplug a controller: it watches for add/remove events too, so
 * it also proves the hotplug path the runtime relies on.
 */
#include <SDL3/SDL.h>
#include <stdio.h>

static void list_pads(void)
{
    int count = 0;
    SDL_JoystickID *ids = SDL_GetJoysticks(&count);

    printf("  joysticks visible: %d\n", count);
    if (ids) {
        for (int i = 0; i < count; i++) {
            SDL_JoystickID id = ids[i];
            const char *name = SDL_GetJoystickNameForID(id);
            bool is_pad = SDL_IsGamepad(id);
            printf("    [%d] id=%u  name=%s  gamepad=%s\n",
                   i, (unsigned)id, name ? name : "(unknown)",
                   is_pad ? "YES" : "no (joystick only)");
            if (is_pad) {
                SDL_Gamepad *g = SDL_OpenGamepad(id);
                printf("         SDL_OpenGamepad -> %s\n", g ? "OK" : SDL_GetError());
                if (g) SDL_CloseGamepad(g);
            }
        }
        SDL_free(ids);
    }
}

int main(int argc, char **argv)
{
    (void)argc; (void)argv;

    printf("SDL version: %d\n", SDL_GetVersion());

    /* Same flags the runtime passes (SDL_INIT_GAMECONTROLLER maps here). */
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        printf("SDL_Init FAILED: %s\n", SDL_GetError());
        return 1;
    }
    printf("SDL_Init(VIDEO|GAMEPAD): OK\n");

    printf("\ninitial scan:\n");
    list_pads();

    printf("\nwatching for hotplug for 20s - plug a controller in now...\n");
    fflush(stdout);

    Uint64 end = SDL_GetTicks() + 20000;
    while (SDL_GetTicks() < end) {
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_EVENT_GAMEPAD_ADDED) {
                printf("  >> GAMEPAD_ADDED   which=%u\n", (unsigned)ev.gdevice.which);
                list_pads();
            } else if (ev.type == SDL_EVENT_GAMEPAD_REMOVED) {
                printf("  >> GAMEPAD_REMOVED which=%u\n", (unsigned)ev.gdevice.which);
            } else if (ev.type == SDL_EVENT_JOYSTICK_ADDED) {
                printf("  >> JOYSTICK_ADDED  which=%u\n", (unsigned)ev.jdevice.which);
            }
            fflush(stdout);
        }
        SDL_Delay(50);
    }

    printf("\nfinal scan:\n");
    list_pads();

    SDL_Quit();
    return 0;
}
