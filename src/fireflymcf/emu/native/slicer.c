/* Instruction budget for Unicorn without its per-instruction count hook.
 *
 * Registered as a UC_HOOK_BLOCK callback: each block charges its size in
 * halfwords (16-bit Thumb = 1, 32-bit = 2) against the slice budget as it
 * starts.  Once the budget is spent the next block stops emulation instead;
 * a stop requested here takes effect before that block runs, so it is not
 * charged.  `used` is thus exactly what ran (blocks cut short by another
 * hook's stop are charged in full).
 *
 * uc_emu_stop is passed in as a function pointer so this needs no link-time
 * dependency on libunicorn.
 */
#include <stdint.h>

typedef int (*stop_fn)(void *uc);

typedef struct {
    int64_t budget;   /* halfwords left in this slice */
    uint64_t used;    /* halfwords charged since last reset */
    stop_fn stop;     /* uc_emu_stop */
} slicer_t;

void slicer_block(void *uc, uint64_t address, uint32_t size, void *user)
{
    slicer_t *s = user;
    uint32_t cost = size >> 1;
    (void)address;
    if (s->budget <= 0) {
        s->stop(uc);
        return;
    }
    s->used += cost;
    s->budget -= cost;
}
