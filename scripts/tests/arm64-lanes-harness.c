/* Compile the full pinned decoder. Gadget symbols are address-only stubs;
 * this harness inspects generated operands, it does not execute the JIT. */
#include DECODER_SOURCE
#include "gadget-stubs.inc"

volatile bool g_trace_highbits;
bool asbestos_should_trace_guest_pc(addr_t pc) { (void)pc; return false; }
void *tlb_handle_miss(struct tlb *tlb, addr_t addr, int type) {
    (void)tlb; (void)addr; (void)type; abort();
}
bool __tlb_read_cross_page(struct tlb *tlb, addr_t addr, char *out, unsigned size) {
    (void)tlb; (void)addr; (void)out; (void)size; abort();
}

static const char *gadget_name(unsigned long address) {
#define TAG(name, label) if (address == (unsigned long)gadget_##name) return label
    TAG(set_jit_saved_pc, "pc");
    TAG(calc_addr_imm, "addr");
    TAG(addr_add_imm, "add");
    TAG(ldr_simd_h, "scalarload");
    TAG(ld1_single_h, "load");
    TAG(st1_single_h, "store");
    TAG(update_base, "update");
    TAG(update_base_reg, "update_reg");
    TAG(simd_store_interleaved, "interleave");
#undef TAG
    return "unexpected";
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        uint32_t insn = (uint32_t)strtoul(argv[i], NULL, 16);
        struct gen_state state = {0};
        state.capacity = 64;
        state.block = calloc(1, sizeof(*state.block) + 64 * sizeof(unsigned long));
        assert(state.block);
        assert(gen_ldst(&state, insn) == 1);
        assert(state.size % 2 == 0);
        printf("%08x", insn);
        for (unsigned j = 0; j < state.size; j += 2)
            printf(" %s:%lx", gadget_name(state.block->code[j]), state.block->code[j+1]);
        puts("");
        free(state.block);
    }
    return 0;
}
