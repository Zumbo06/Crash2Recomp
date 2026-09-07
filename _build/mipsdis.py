"""mipsdis.py - disassemble a recompiled function from the generated C.

    python _build/mipsdis.py func 800439D4        # one function, bounded by .ranges
    python _build/mipsdis.py range 80043A84 80043B40

Pulls the `/* 0xPC: 0xENCODING */` pairs the recompiler leaves on every
instruction (branches carry a different comment, so those are recovered from
the MIPS encoding of the missing PCs' neighbours being impossible - they are
listed as GAP and are jumps/branches whose delay slot follows). Marks the
lines that matter for the Night Fight light: MTC2 to IR0, GTE ops, DPCS.
"""
import bisect, glob, os, re, sys
PROJ = r"C:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
R = "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split()
def r(n): return "$" + R[n]
def s16(v): return v - 0x10000 if v & 0x8000 else v
SPEC = {0:"sll",2:"srl",3:"sra",4:"sllv",6:"srlv",7:"srav",8:"jr",9:"jalr",12:"syscall",13:"break",16:"mfhi",17:"mthi",18:"mflo",19:"mtlo",24:"mult",25:"multu",26:"div",27:"divu",32:"add",33:"addu",34:"sub",35:"subu",36:"and",37:"or",38:"xor",39:"nor",42:"slt",43:"sltu"}
IMM = {8:"addi",9:"addiu",10:"slti",11:"sltiu",12:"andi",13:"ori",14:"xori"}
MEM = {32:"lb",33:"lh",34:"lwl",35:"lw",36:"lbu",37:"lhu",38:"lwr",40:"sb",41:"sh",42:"swl",43:"sw",46:"swr",50:"lwc2",58:"swc2"}
GTE = {0x01:"RTPS",0x06:"NCLIP",0x0C:"OP",0x10:"DPCS",0x11:"INTPL",0x12:"MVMVA",0x13:"NCDS",0x14:"CDP",0x16:"NCDT",0x1B:"NCCS",0x1C:"CC",0x1E:"NCS",0x20:"NCT",0x28:"SQR",0x29:"DCPL",0x2A:"DPCT",0x2D:"AVSZ3",0x2E:"AVSZ4",0x30:"RTPT",0x3D:"GPF",0x3E:"GPL",0x3F:"NCCT"}
DATA = {0:"VXY0",1:"VZ0",2:"VXY1",3:"VZ1",4:"VXY2",5:"VZ2",6:"RGBC",7:"OTZ",8:"IR0",9:"IR1",10:"IR2",11:"IR3",12:"SXY0",13:"SXY1",14:"SXY2",15:"SXYP",16:"SZ0",17:"SZ1",18:"SZ2",19:"SZ3",20:"RGB0",21:"RGB1",22:"RGB2",24:"MAC0",25:"MAC1",26:"MAC2",27:"MAC3"}
CTRL = {21:"RFC",22:"GFC",23:"BFC",24:"OFX",25:"OFY",26:"H",27:"DQA",28:"DQB",29:"ZSF3",30:"ZSF4",31:"FLAG"}
def dis(pc, w):
    op=w>>26; rs=(w>>21)&31; rt=(w>>16)&31; rd=(w>>11)&31; sh=(w>>6)&31; fn=w&63; imm=w&0xFFFF; si=s16(imm)
    if w==0: return "nop"
    if op==0:
        m=SPEC.get(fn,"spec%d"%fn)
        if fn in (0,2,3): return f"{m} {r(rd)}, {r(rt)}, {sh}"
        if fn in (4,6,7): return f"{m} {r(rd)}, {r(rt)}, {r(rs)}"
        if fn==8: return f"jr {r(rs)}"
        if fn==9: return f"jalr {r(rd)}, {r(rs)}"
        if fn in (12,13): return m
        if fn in (16,18): return f"{m} {r(rd)}"
        if fn in (17,19): return f"{m} {r(rs)}"
        if fn in (24,25,26,27): return f"{m} {r(rs)}, {r(rt)}"
        return f"{m} {r(rd)}, {r(rs)}, {r(rt)}"
    if op==1: return {0:"bltz",1:"bgez",16:"bltzal",17:"bgezal"}.get(rt,"regimm")+f" {r(rs)}, 0x{pc+4+(si<<2):08X}"
    if op in (2,3): return f"{'j' if op==2 else 'jal'} 0x{((pc+4)&0xF0000000)|((w&0x3FFFFFF)<<2):08X}"
    if op==4: return f"beq {r(rs)}, {r(rt)}, 0x{pc+4+(si<<2):08X}"
    if op==5: return f"bne {r(rs)}, {r(rt)}, 0x{pc+4+(si<<2):08X}"
    if op==6: return f"blez {r(rs)}, 0x{pc+4+(si<<2):08X}"
    if op==7: return f"bgtz {r(rs)}, 0x{pc+4+(si<<2):08X}"
    if op in IMM: return f"{IMM[op]} {r(rt)}, {r(rs)}, " + (f"0x{imm:04X}" if op in (12,13,14) else str(si))
    if op==15: return f"lui {r(rt)}, 0x{imm:04X}"
    if op==16: return f"cop0 rs={rs} {r(rt)} rd={rd}"
    if op==18:
        if w & (1<<25): return f"gte {GTE.get(fn,'0x%02X'%fn)} (sf={(w>>19)&1} lm={(w>>10)&1})"
        return {0:"mfc2",2:"cfc2",4:"mtc2",6:"ctc2"}.get(rs,"cop2?")+f" {r(rt)}, {(DATA if rs in (0,4) else CTRL).get(rd,'r%d'%rd)}"
    if op in MEM: return f"{MEM[op]} {r(rt)}, {si}({r(rs)})"
    return "??"
def funcs():
    return sorted(int(l.split()[1],16) for l in open(os.path.join(PROJ,"generated","SCUS_941.54_full.ranges")) if l.startswith("F "))
def encodings(lo, hi):
    pat = re.compile(r"/\* 0x([0-9A-F]{8}): 0x([0-9A-F]{8}) \*/")
    out = {}
    for path in glob.glob(os.path.join(PROJ,"generated","*.c")) + glob.glob(os.path.join(PROJ,"build-clang","cache","**","*_patched.c"), recursive=True):
        try: text = open(path, encoding="utf-8", errors="replace").read()
        except OSError: continue
        for m in pat.finditer(text):
            pc = int(m.group(1),16)
            if lo <= pc < hi: out[pc] = int(m.group(2),16)
    return out
def show(lo, hi):
    enc = encodings(lo, hi)
    for pc in range(lo, hi, 4):
        if pc in enc:
            w = enc[pc]; d = dis(pc, w); mark = ""
            if "mtc2" in d and "IR0" in d: mark = "   <== IR0 (light factor)"
            elif d.startswith("gte DPCS") or d.startswith("gte DPCT"): mark = "   <== depth cue"
            elif d.startswith("gte ") or "ctc2" in d or "mtc2" in d or "mfc2" in d: mark = "   <-- gte"
            print(f"{pc:08X}  {w:08X}  {d}{mark}")
        else:
            print(f"{pc:08X}  ........  GAP (branch/jump; next line is its delay slot)")
if __name__ == "__main__":
    F = funcs()
    if sys.argv[1] == "func":
        lo = int(sys.argv[2],16); i = bisect.bisect_right(F, lo); hi = F[i] if i < len(F) else lo + 0x400
        print(f"=== func_{lo:08X}  [{lo:08X}..{hi:08X})  {(hi-lo)//4} instructions ===")
        show(lo, hi)
    else:
        show(int(sys.argv[2],16), int(sys.argv[3],16))
