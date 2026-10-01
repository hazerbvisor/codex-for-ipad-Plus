#!/usr/bin/env python3
"""Emit a static guest fixture executing PMULL/PMULL2, including aliases."""
import argparse
from pathlib import Path


def generate():
    cases = []
    for size in (0, 3):
        for upper in (0, 1):
            for dest, rn, rm in [(2, 0, 1), (0, 0, 1), (1, 0, 1), (0, 0, 0)]:
                number = len(cases)
                source = '16b' if upper else '8b'
                target = '8h'
                if size == 3:
                    source, target = ('2d' if upper else '1d'), '1q'
                mnemonic = f'pmull{"2" if upper else ""} v{dest}.{target}, v{rn}.{source}, v{rm}.{source}'
                cases.append((size, upper, dest, rn, rm, mnemonic, number))
    code = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint64_t seed=4500;
static uint8_t random_byte(void) { seed=seed*6364136223846793005ULL+1; return seed>>56; }
static __uint128_t polynomial(uint64_t a,uint64_t b) {
    __uint128_t r=0;
    for(int i=0;i<64;i++) for(int j=0;j<64;j++)
        if((a>>i&1)&&(b>>j&1)) r^=(__uint128_t)1<<(i+j);
    return r;
}
static void execute(int op,uint8_t *regs) {
    switch(op) {
'''
    for size, upper, dest, rn, rm, mnemonic, number in cases:
        encoding = 0x0e20e000 | upper << 30 | size << 22 | rm << 16 | rn << 5 | dest
        code += f'''case {number}: __asm__ volatile(
            "ldp q0, q1, [%0]\\nldr q2, [%0, #32]\\n"
            ".inst 0x{encoding:08x}\\n" /* {mnemonic} */
            "stp q0, q1, [%0]\\nstr q2, [%0, #32]"
            : : "r"(regs) : "v0", "v1", "v2", "memory"); break;
'''
    code += '    }\n}\nstatic const int cases[][5]={\n'
    code += ',\n'.join('{' + ','.join(str(v) for v in case[:5]) + '}' for case in cases)
    code += r'''
};
int main(void) {
    int failures=0,count=0;
    for(int op=0;op<16;op++) for(int round=0;round<64;round++) {
        uint8_t regs[48],expected[48];
        for(int i=0;i<48;i++) regs[i]=random_byte();
        memcpy(expected,regs,48);
        int size=cases[op][0],upper=cases[op][1],dest=cases[op][2];
        const uint8_t *n=regs+16*cases[op][3]+8*upper;
        const uint8_t *m=regs+16*cases[op][4]+8*upper;
        if(size==0) {
            for(int i=0;i<8;i++) {uint16_t r=polynomial(n[i],m[i]);memcpy(expected+16*dest+2*i,&r,2);}
        } else {
            uint64_t a,b;memcpy(&a,n,8);memcpy(&b,m,8);
            __uint128_t r=polynomial(a,b);memcpy(expected+16*dest,&r,16);
        }
        execute(op,regs);count++;
        failures+=memcmp(regs,expected,48)!=0;
    }
    printf("cases=%d failures=%d\n",count,failures);
    return failures!=0;
}
'''
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(generate())
