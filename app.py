import streamlit as st
from datetime import date, datetime
import pandas as pd
import re, io, hashlib
from pypdf import PdfReader
from supabase import create_client

st.set_page_config(page_title="S&S Group · Control de Cuentas", layout="wide")

# ---------- Seguridad ----------
def check_password():
    expected = str(st.secrets.get("APP_PASSWORD", "")).strip()
    if not expected:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("S&S Group · Control de Cuentas")
    pwd = st.text_input("Contraseña", type="password")
    if st.button("Ingresar", type="primary"):
        if pwd == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    return False

if not check_password():
    st.stop()

# ---------- Supabase ----------
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

def rows(table, select="*", order=None, desc=False):
    q = sb.table(table).select(select)
    if order:
        q = q.order(order, desc=desc)
    res = q.execute()
    return res.data or []

def df_rows(table, select="*", order=None, desc=False):
    return pd.DataFrame(rows(table, select, order, desc))

def insert_row(table, payload):
    return sb.table(table).insert(payload).execute().data

def update_rows(table, payload, eq_col, eq_val):
    return sb.table(table).update(payload).eq(eq_col, eq_val).execute().data

def money(v):
    try:
        return f"$ {float(v):,.0f}".replace(",", ".")
    except:
        return "$ 0"

def get_clientes(active_only=True):
    q = sb.table("clientes").select("*").order("nombre")
    if active_only:
        q = q.eq("activo", True)
    data = q.execute().data or []
    return pd.DataFrame(data)

def get_movimientos():
    mov = rows("movimientos", "*", "fecha", True)
    cli = rows("clientes", "id,nombre")
    cmap = {int(c["id"]): c["nombre"] for c in cli}
    for m in mov:
        m["cliente"] = cmap.get(int(m["cliente_id"]), "")
    return pd.DataFrame(mov)

def balances_df():
    clientes = get_clientes(True)
    mov = df_rows("movimientos")
    if clientes.empty:
        return pd.DataFrame(columns=["id","nombre","modalidad","honorario","saldo"])
    if mov.empty:
        clientes["saldo"] = 0.0
    else:
        sums = mov.groupby("cliente_id")["importe"].sum()
        clientes["saldo"] = clientes["id"].map(sums).fillna(0).astype(float)
    return clientes[["id","nombre","modalidad","honorario","saldo"]].sort_values(["saldo","nombre"], ascending=[False,True])

# ---------- PDF / ARCA ----------
def extract_pdf_text(uploaded):
    try:
        reader = PdfReader(uploaded)
        return "\n".join((p.extract_text() or "") for p in reader.pages[:4])
    except Exception:
        return ""

def parse_arg_money(s):
    if not s: return None
    s = s.strip().replace("$","").replace(" ","")
    if "," in s and "." in s:
        s = s.replace(".","").replace(",",".")
    elif "," in s:
        s = s.replace(",",".")
    elif re.match(r"^\d{1,3}(\.\d{3})+$", s):
        s = s.replace(".","")
    try: return float(s)
    except: return None

def parse_invoice(text):
    out = {"cuit":None,"cliente_detectado":None,"fecha":None,"comprobante":None,"importe":None,"tipo_comprobante":"Factura"}
    if not text: return out
    clean = text.replace("\xa0"," ")
    upper = clean.upper()
    if "NOTA DE CREDITO" in upper or "NOTA DE CRÉDITO" in upper:
        out["tipo_comprobante"] = "Omitir"
        return out

    dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", clean)
    if dates:
        try: out["fecha"] = datetime.strptime(dates[0], "%d/%m/%Y").date()
        except: pass

    m = re.search(r"Punto de Venta:\s*(?:Comp\.\s*Nro:\s*)?(\d{5})\s+(\d{8})", clean, re.I)
    if not m: m = re.search(r"(\d{5})\s+(\d{8})", clean)
    if m: out["comprobante"] = f"{m.group(1)}-{m.group(2)}"

    issuer_cuits = {"20351407248"}
    cuits = re.findall(r"CUIT:\s*([0-9\-]{11,13})", clean, re.I)
    normalized = [re.sub(r"\D","",x) for x in cuits]
    out["cuit"] = next((x for x in normalized if len(x)==11 and x not in issuer_cuits), None)

    lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]
    for i,ln in enumerate(lines):
        if re.sub(r"\D","",ln) in issuer_cuits:
            for cand in lines[i+1:i+5]:
                cu = cand.upper()
                if cand and cu != "SIRVENT MARTIN NICOLAS" and not cu.startswith(("CUIT","TRANSFERENCIA","PUNTO DE VENTA")) and not re.match(r"^\d",cand) and len(cand)>=5:
                    out["cliente_detectado"] = cand
                    break
            if out["cliente_detectado"]: break

    monetary = re.findall(r"(?<!\d)(\d+(?:\.\d{3})*,\d{2}|\d+,\d{2})(?!\d)", clean)
    vals = [parse_arg_money(x) for x in monetary]
    vals = [x for x in vals if x is not None and x>=0]
    if vals: out["importe"] = max(vals)
    return out

def resolve_cliente(parsed, filename):
    clientes = get_clientes(True)
    if clientes.empty: return None,None
    if parsed.get("cuit"):
        normc = clientes["cuit"].fillna("").astype(str).str.replace(r"\D","",regex=True)
        m = clientes[normc == parsed["cuit"]]
        if len(m)==1: return int(m.iloc[0]["id"]), m.iloc[0]["nombre"]
    detected = (parsed.get("cliente_detectado") or "").strip().lower()
    if detected:
        def norm(s): return re.sub(r"[^a-z0-9]","",str(s).lower())
        nd=norm(detected)
        for _,r in clientes.iterrows():
            nr=norm(r["nombre"])
            if nr==nd or (len(nr)>=8 and (nr in nd or nd in nr)):
                return int(r["id"]),r["nombre"]
    low=filename.lower()
    for _,r in clientes.iterrows():
        words=[w.lower() for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9]+",r["nombre"]) if len(w)>=5]
        if any(w in low for w in words):
            return int(r["id"]),r["nombre"]
    return None,None

def save_pdf(raw, original_name):
    safe = re.sub(r"[^A-Za-z0-9_.-]","_",original_name)
    digest = hashlib.sha1(raw).hexdigest()[:10]
    path = f"{datetime.now().strftime('%Y/%m')}/{datetime.now().strftime('%Y%m%d%H%M%S')}_{digest}_{safe}"
    sb.storage.from_("facturas").upload(path, raw, {"content-type":"application/pdf","upsert":"false"})
    return path

def generate_monthly_notices(period):
    clientes = get_clientes(True)
    if clientes.empty: return 0
    clientes = clientes[clientes["modalidad"]=="Aviso de pago"]
    generated=0
    for _,r in clientes.iterrows():
        comp=f"AVISO-{period.strftime('%Y%m')}"
        exists = sb.table("movimientos").select("id").eq("cliente_id",int(r["id"])).eq("tipo","Aviso de pago").eq("periodo",period.isoformat()).eq("comprobante",comp).execute().data
        if exists: continue
        dia=min(int(r.get("dia_generacion") or 1),28)
        insert_row("movimientos",{
            "fecha":period.replace(day=dia).isoformat(),
            "cliente_id":int(r["id"]),
            "tipo":"Aviso de pago",
            "descripcion":f"Honorarios {period.strftime('%m/%Y')}",
            "importe":float(r.get("honorario") or 0),
            "periodo":period.isoformat(),
            "comprobante":comp,
            "creado_en":datetime.now().isoformat()
        })
        generated+=1
    return generated

# ---------- UI ----------
st.title("S&S Group · Control de Cuentas")
st.caption("Versión online · datos persistentes en Supabase")

tabs = st.tabs(["Panel","Facturas","Avisos de pago","Pagos","Trabajos extras","Clientes","Movimientos","Exportar"])

with tabs[0]:
    saldos=balances_df()
    c1,c2,c3,c4=st.columns(4)
    total=float(saldos["saldo"].clip(lower=0).sum()) if not saldos.empty else 0
    c1.metric("Total pendiente", f"{money(total)} ({money(total/2)})", help="Entre paréntesis: 50% correspondiente a Martín.")
    c2.metric("Clientes activos",len(saldos))
    c3.metric("Clientes con deuda",int((saldos["saldo"]>0).sum()) if not saldos.empty else 0)
    c4.metric("Movimientos",len(rows("movimientos","id")))
    st.subheader("Estado por cliente")
    show=saldos.copy()
    if not show.empty:
        show["honorario"]=show["honorario"].map(money)
        show["saldo"]=show["saldo"].map(money)
        st.dataframe(show[["nombre","modalidad","honorario","saldo"]],use_container_width=True,hide_index=True)

with tabs[1]:
    st.subheader("Carga masiva de facturas")
    files=st.file_uploader("Facturas PDF",type=["pdf"],accept_multiple_files=True)
    if files:
        parsed_rows=[]; file_map={}
        for f in files:
            raw=f.getvalue(); file_map[f.name]=raw
            parsed=parse_invoice(extract_pdf_text(io.BytesIO(raw)))
            cid,cname=resolve_cliente(parsed,f.name)
            if parsed["tipo_comprobante"]=="Omitir": estado="OMITIR"
            elif cid and parsed["importe"]: estado="OK"
            elif parsed.get("cliente_detectado") and parsed["importe"]: estado="CLIENTE NUEVO"
            else: estado="REVISAR"
            parsed_rows.append({
                "archivo":f.name,"cliente_id":cid,"cliente":cname or "",
                "cliente_detectado":parsed.get("cliente_detectado") or "",
                "cuit":parsed.get("cuit") or "","fecha":parsed["fecha"] or date.today(),
                "comprobante":parsed["comprobante"] or "","importe":parsed["importe"] or 0.0,"estado":estado
            })
        df=pd.DataFrame(parsed_rows)
        st.dataframe(df[["archivo","cliente","cliente_detectado","cuit","fecha","comprobante","importe","estado"]],use_container_width=True,hide_index=True)

        nuevos=[r for r in parsed_rows if r["estado"]=="CLIENTE NUEVO"]
        if nuevos and st.button("Crear clientes nuevos detectados"):
            creados=0
            for r in nuevos:
                nombre=(r["cliente_detectado"] or "").strip()
                if not nombre: continue
                try:
                    insert_row("clientes",{
                        "nombre":nombre,"cuit":r["cuit"] or None,"modalidad":"Factura",
                        "honorario":0,"vigente_desde":date.today().replace(day=1).isoformat(),
                        "dia_generacion":1,"activo":True
                    }); creados+=1
                except Exception: pass
            st.success(f"Clientes creados: {creados}. Volvé a cargar el lote para vincularlos.")

        st.caption("Las notas de crédito quedan omitidas.")
        if st.button("Confirmar lote reconocido",type="primary"):
            ok=0; errs=[]
            for r in parsed_rows:
                if not r["cliente_id"] or not r["importe"] or r["estado"]!="OK": continue
                try:
                    pdf_path=save_pdf(file_map[r["archivo"]],r["archivo"])
                    f=r["fecha"]; periodo=date(f.year,f.month,1)
                    insert_row("movimientos",{
                        "fecha":f.isoformat(),"cliente_id":int(r["cliente_id"]),"tipo":"Factura/Cargo",
                        "descripcion":"Factura","importe":float(r["importe"]),"periodo":periodo.isoformat(),
                        "comprobante":r["comprobante"] or r["archivo"],"pdf_path":pdf_path,
                        "creado_en":datetime.now().isoformat()
                    }); ok+=1
                except Exception:
                    errs.append(r["archivo"])
            st.success(f"Facturas cargadas: {ok}")
            if errs: st.warning("No se cargaron (posible duplicado o error): "+", ".join(errs))

    st.divider()
    st.subheader("Carga manual de una factura")
    clientes=get_clientes(True)
    if not clientes.empty:
        with st.form("factura_manual"):
            nom=st.selectbox("Cliente",clientes["nombre"].tolist())
            fecha=st.date_input("Fecha",value=date.today())
            comp=st.text_input("Comprobante")
            importe=st.number_input("Importe",min_value=0.0,step=1000.0)
            archivo=st.file_uploader("PDF opcional",type=["pdf"],key="manual_pdf")
            if st.form_submit_button("Guardar factura"):
                cid=int(clientes.loc[clientes["nombre"]==nom,"id"].iloc[0])
                pdf_path=save_pdf(archivo.getvalue(),archivo.name) if archivo else None
                insert_row("movimientos",{
                    "fecha":fecha.isoformat(),"cliente_id":cid,"tipo":"Factura/Cargo","descripcion":"Factura",
                    "importe":importe,"periodo":fecha.replace(day=1).isoformat(),
                    "comprobante":comp or None,"pdf_path":pdf_path,"creado_en":datetime.now().isoformat()
                })
                st.success("Factura cargada.")

with tabs[2]:
    st.subheader("Avisos de pago")
    periodo=st.date_input("Período",value=date.today().replace(day=1),key="av_periodo")
    if st.button("Generar avisos del período"):
        st.success(f"Avisos nuevos generados: {generate_monthly_notices(periodo.replace(day=1))}")
    saldos=balances_df()
    if not saldos.empty:
        avisos=saldos[saldos["modalidad"]=="Aviso de pago"].copy()
        avisos["honorario"]=avisos["honorario"].map(money); avisos["saldo"]=avisos["saldo"].map(money)
        st.dataframe(avisos[["nombre","honorario","saldo"]],use_container_width=True,hide_index=True)

with tabs[3]:
    st.subheader("Registrar pago")
    clientes=get_clientes(True)
    if not clientes.empty:
        with st.form("pago"):
            nom=st.selectbox("Cliente",clientes["nombre"].tolist(),key="pg_cli")
            fecha=st.date_input("Fecha del pago",value=date.today(),key="pg_fecha")
            imp=st.number_input("Importe recibido",min_value=0.0,step=1000.0,key="pg_imp")
            desc=st.text_input("Descripción / referencia",value="Pago recibido")
            if st.form_submit_button("Registrar pago",type="primary"):
                cid=int(clientes.loc[clientes["nombre"]==nom,"id"].iloc[0])
                insert_row("movimientos",{
                    "fecha":fecha.isoformat(),"cliente_id":cid,"tipo":"Pago","descripcion":desc,
                    "importe":-abs(imp),"periodo":fecha.replace(day=1).isoformat(),
                    "estado_conciliacion":"Registrado","creado_en":datetime.now().isoformat()
                }); st.success("Pago registrado.")

with tabs[4]:
    st.subheader("Trabajo extra / puntual")
    clientes=get_clientes(True)
    if not clientes.empty:
        with st.form("extra"):
            nom=st.selectbox("Cliente",clientes["nombre"].tolist(),key="ex_cli")
            fecha=st.date_input("Fecha",value=date.today(),key="ex_fecha")
            concepto=st.text_input("Concepto",placeholder="Ej.: Medición de ruido extraordinaria")
            imp=st.number_input("Importe",min_value=0.0,step=1000.0,key="ex_imp")
            comp=st.text_input("Comprobante / referencia opcional")
            if st.form_submit_button("Guardar trabajo"):
                cid=int(clientes.loc[clientes["nombre"]==nom,"id"].iloc[0])
                insert_row("movimientos",{
                    "fecha":fecha.isoformat(),"cliente_id":cid,"tipo":"Ajuste",
                    "descripcion":concepto or "Trabajo puntual","importe":imp,
                    "periodo":fecha.replace(day=1).isoformat(),"comprobante":comp or None,
                    "creado_en":datetime.now().isoformat()
                }); st.success("Trabajo puntual registrado.")

with tabs[5]:
    st.subheader("Clientes")
    clientes=get_clientes(False)
    if not clientes.empty: st.dataframe(clientes,use_container_width=True,hide_index=True)
    c1,c2=st.columns(2)
    with c1:
        st.markdown("#### Nuevo cliente")
        with st.form("nuevo_cliente"):
            n=st.text_input("Nombre"); cuit=st.text_input("CUIT")
            mod=st.selectbox("Modalidad",["Factura","Aviso de pago"])
            hon=st.number_input("Honorario vigente",min_value=0.0,step=1000.0)
            vd=st.date_input("Vigente desde",value=date.today().replace(day=1))
            if st.form_submit_button("Crear cliente"):
                insert_row("clientes",{"nombre":n,"cuit":cuit or None,"modalidad":mod,"honorario":hon,
                    "vigente_desde":vd.isoformat(),"dia_generacion":1,"activo":True})
                st.success("Cliente creado.")
    with c2:
        st.markdown("#### Cambiar honorario")
        activos=get_clientes(True)
        if not activos.empty:
            with st.form("cambio_honorario"):
                nom=st.selectbox("Cliente",activos["nombre"].tolist(),key="ch_cli")
                nuevo=st.number_input("Nuevo honorario",min_value=0.0,step=1000.0,key="ch_imp")
                vig=st.date_input("Vigente desde",value=date.today().replace(day=1),key="ch_vig")
                nota=st.text_input("Nota",value="Actualización manual")
                if st.form_submit_button("Guardar nuevo honorario"):
                    cid=int(activos.loc[activos["nombre"]==nom,"id"].iloc[0])
                    insert_row("honorarios",{"cliente_id":cid,"vigente_desde":vig.isoformat(),"honorario":nuevo,"nota":nota})
                    update_rows("clientes",{"honorario":nuevo,"vigente_desde":vig.isoformat()},"id",cid)
                    st.success("Honorario actualizado. Los movimientos anteriores no cambian.")

with tabs[6]:
    st.subheader("Movimientos")
    mov=get_movimientos()
    if not mov.empty:
        mov["importe_fmt"]=mov["importe"].map(money)
        cols=["id","fecha","cliente","tipo","descripcion","importe_fmt","periodo","comprobante","pdf_path"]
        st.dataframe(mov[[c for c in cols if c in mov.columns]],use_container_width=True,hide_index=True)

with tabs[7]:
    st.subheader("Exportar a Excel")
    if st.button("Preparar Excel"):
        clientes=get_clientes(False)
        movimientos=get_movimientos()
        saldos=balances_df()
        honorarios=df_rows("honorarios",order="vigente_desde",desc=True)
        out=io.BytesIO()
        with pd.ExcelWriter(out,engine="openpyxl") as writer:
            clientes.to_excel(writer,index=False,sheet_name="Clientes")
            movimientos.to_excel(writer,index=False,sheet_name="Movimientos")
            saldos.to_excel(writer,index=False,sheet_name="Saldos")
            honorarios.to_excel(writer,index=False,sheet_name="Honorarios")
        st.download_button("Descargar Excel",out.getvalue(),f"Control_cuentas_{date.today().isoformat()}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
