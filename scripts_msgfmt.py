
import sys, struct, io, re
def parse_po(po_text:str):
    msgs = {}
    msgid = None
    msgstr = None
    lines = iter(po_text.splitlines())
    def unq(s): return bytes(s[1:-1], "utf-8").decode("unicode_escape")
    for line in lines:
        if line.startswith("msgid "):
            msgid = unq(line[5:].strip())
            cont=[]
            for l in lines:
                if l.startswith("msgstr "):
                    msgstr = unq(l[6:].strip()); break
                if l.startswith('"'): cont.append(unq(l.strip()))
                else: break
            if cont: msgid += "".join(cont)
            cont=[]
            for l in lines:
                if l.startswith('"'):
                    cont.append(unq(l.strip()))
                else:
                    msgs[msgid]=msgstr+("".join(cont) if cont else "")
                    msgid=None; msgstr=None
                    if l.startswith("msgid "):
                        msgid = unq(l[5:].strip()); cont=[]
                        for l2 in lines:
                            if l2.startswith("msgstr "):
                                msgstr = unq(l2[6:].strip()); break
                            if l2.startswith('"'): cont.append(unq(l2.strip()))
                            else: break
                        if cont: msgid += "".join(cont)
                        cont=[]
                        for l2 in lines:
                            if l2.startswith('"'): cont.append(unq(l2.strip()))
                            else:
                                msgs[msgid]=msgstr+("".join(cont) if cont else ""); msgid=None; msgstr=None
                                break
                    break
    if msgid is not None:
        msgs[msgid]=msgstr or ""
    return msgs
def write_mo(msgs, out):
    KEYS=list(msgs.keys())
    ids=b"\x00".join(s.encode("utf-8") for s in KEYS)+b"\x00"
    strs=b"\x00".join(msgs[k].encode("utf-8") for k in KEYS)+b"\x00"
    keystart=7*4+16*len(KEYS)
    valstart=keystart+len(ids)
    koffsets=[]; voffsets=[]
    off=0
    for k in KEYS:
        bts=k.encode("utf-8")+b"\x00"
        koffsets.append((len(bts)-1, keystart+off)); off+=len(bts)
    off=0
    for k in KEYS:
        bts=msgs[k].encode("utf-8")+b"\x00"
        voffsets.append((len(bts)-1, valstart+off)); off+=len(bts)
    out.write(struct.pack("Iiiiiii",0x950412de,0, len(KEYS), 7*4, 7*4+8*len(KEYS), 0,0))
    for ln,ofs in koffsets: out.write(struct.pack("II", ln, ofs))
    for ln,ofs in voffsets: out.write(struct.pack("II", ln, ofs))
    out.write(ids); out.write(strs)
def compile_po_to_mo(src, dst):
    text=open(src,"r",encoding="utf-8").read()
    msgs=parse_po(text)
    with open(dst,"wb") as f: write_mo(msgs,f)
if __name__=="__main__":
    compile_po_to_mo(sys.argv[1], sys.argv[2])
