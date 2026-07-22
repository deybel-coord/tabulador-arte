# -*- coding: utf-8 -*-
# build.py - Baja el CSV publicado, reconstruye el dashboard (index.html).
# Uso: python build.py   (requiere pandas, numpy)

# -*- coding: utf-8 -*-
import pandas as pd, re, json, numpy as np

import os
# ================== ORIGEN DE LOS DATOS ==================
# Pega aqui la URL del CSV PUBLICADO de tu Google Sheet
# (Archivo -> Compartir -> Publicar en la web -> CSV).
# Tambien puedes definir la variable de entorno CSV_URL para sobreescribirla.
CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vToeiwDkNiA157XK8l0CMiDNuehd71dzGwASKv_HTeXOSYwf0OB3Y7kJfrLP7B7OzmY_2g8QeAiBMaz/pub?output=csv"
SOURCE = os.environ.get("CSV_URL", CSV_URL)
print("Leyendo datos de:", SOURCE)
df = pd.read_csv(SOURCE)
cols = list(df.columns)

def norm_puesto(x):
    if pd.isna(x): return None
    x = str(x).strip()
    m = {'Decoradora':'Decorador(a)', 'Decorador(a)':'Decorador(a)',
         'Coordinadora':'Coordinador(a)', 'Coordinador(a)':'Coordinador(a)'}
    return m.get(x, x)

# --- Deteccion AUTOMATICA de puesto (robusta a cambios de columnas) ---
# El formulario reubica la pregunta de puesto con el tiempo, a veces en columnas
# SIN nombre. Detectamos el puesto por VALOR: cualquier columna cuyos datos sean
# mayormente etiquetas de puesto conocidas cuenta como columna de puesto.
_named_puesto = [c for c in cols if str(c).strip().startswith('¿Cuál es tu puesto principal?')]
_known = set()
for c in cols:                                     # roles que tienen columna de sueldo
    m = re.search(r'como (.*?)\? \(MXN\)', str(c))
    if m: _known.add(norm_puesto(m.group(1)))
for c in _named_puesto:                             # valores de las columnas nombradas
    for v in df[c].dropna().astype(str).str.strip():
        _known.add(norm_puesto(v))
_known = {k for k in _known if k}

puesto_cols = []
for c in cols:
    name = str(c).strip()
    if name.startswith('¿Cuál es tu puesto principal?'):
        puesto_cols.append(c); continue
    if any(w in name.lower() for w in ('ganas', 'tabulador', 'debería', 'deberia', 'viático', 'viatico', 'pagado')):
        continue
    vals = df[c].dropna().astype(str).str.strip()
    if len(vals) >= 3 and vals.map(lambda v: norm_puesto(v) in _known).mean() > 0.8:
        puesto_cols.append(c)
if not puesto_cols:
    raise SystemExit('No encontre ninguna columna de puesto')

# columnas nombradas tienen prioridad sobre las sin nombre
puesto_cols.sort(key=lambda c: str(c).strip().startswith('¿Cuál es tu puesto principal?'))
_pser = None
for pc in puesto_cols:
    s = df[pc].map(norm_puesto)
    _pser = s if _pser is None else s.combine_first(_pser)
df['PUESTO'] = _pser

_tipos_col = next((c for c in cols if 'tipos de proyecto' in str(c)), cols[1])
df['TIPOS'] = df[_tipos_col].fillna('').apply(lambda s: [t.strip() for t in str(s).split(';') if t.strip()])

_abierta_col = next((c for c in cols if 'Algo más que quieras agregar' in str(c)), cols[-2])
df['ABIERTA'] = df[_abierta_col]

def to_amount(x):
    if pd.isna(x): return np.nan
    s = str(x).lower().strip()
    if s in ('', '—', '-', 'nan'): return np.nan
    s2 = s.replace(',', '').replace('$', '').replace(' ', '')
    mk = re.search(r'(\d+(?:\.\d+)?)\s*k', s)
    if mk: return float(mk.group(1)) * 1000
    if 'mil' in s:
        mm = re.search(r'(\d+(?:\.\d+)?)', s2)
        if mm:
            v = float(mm.group(1)); return v*1000 if v < 100 else v
    m = re.search(r'(\d+(?:\.\d+)?)', s2)
    return float(m.group(1)) if m else np.nan

def rnd(v, base):
    if v is None or (isinstance(v,float) and np.isnan(v)): return None
    return int(round(v/base)*base)

# ---------- VIÁTICOS montos deseados ----------
vi_local_c = [c for c in cols if 'comida (local)' in c and 'de cu' in c.lower()][0]
vi_fuera_c = [c for c in cols if 'fuera de CDMX' in c and 'de cu' in c.lower()][0]
vi_taxi_c  = [c for c in cols if 'Apoyo para taxi' in c and 'de cu' in c.lower()][0]
def clean_viatico(x):
    if pd.isna(x): return np.nan
    m = re.search(r'(\d[\d,\.]*)', str(x).replace(' ',''))
    if not m: return np.nan
    v = float(m.group(1).replace(',',''))
    return np.nan if v > 2000 else v
df['VI_LOCAL']=df[vi_local_c].apply(clean_viatico)
df['VI_FUERA']=df[vi_fuera_c].apply(clean_viatico)
df['VI_TAXI'] =df[vi_taxi_c].apply(clean_viatico)

# ---------- frecuencia de pago ----------
pago_cols=[c for c in cols if '¿Te los han pagado' in c]
concepts={'Viático x comida (local)':[], 'Viático x comida (fuera de CDMX)':[], 'Apoyo para taxi':[]}
for c in pago_cols:
    for k in concepts:
        if k in c: concepts[k].append(c)
freq_order=['Sí, siempre','Casi siempre','A veces','Nunca']
def freq_counts(cn):
    vals=pd.concat([df[c] for c in cn]).dropna().astype(str).str.strip()
    out={f:0 for f in freq_order}
    for v in vals:
        if v in out: out[v]+=1
    return out
pago_freq={k:freq_counts(v) for k,v in concepts.items()}

# ---------- frecuencia de pago POR TIPO DE PRODUCCIÓN ----------
tipos_prod=['Publicidad Nacional','Publicidad Service','Serie o Película']
pago_by_tipo={tp:{'comida':{f:0 for f in freq_order},'taxi':{f:0 for f in freq_order}} for tp in tipos_prod}
for c in pago_cols:
    tp=next((t for t in tipos_prod if c.startswith('['+t+']')),None)
    if tp is None: continue
    grupo='taxi' if 'Apoyo para taxi' in c else 'comida'
    for v in df[c].dropna().astype(str).str.strip():
        if v in freq_order: pago_by_tipo[tp][grupo][v]+=1

# ---------- SUELDOS ----------
tipos=['Publicidad Nacional','Publicidad Service','Serie o Película']
salary_map={}
roles=[]                                    # se detectan AUTOMATICAMENTE de las columnas
for idx,c in enumerate(cols):
    m=re.search(r'\[(.*?)\] ¿Cuánto ganas actualmente como (.*?)\? \(MXN\)', c)
    if m:
        salary_map[(m.group(1),m.group(2))]=(c, cols[idx+1] if idx+1<len(cols) else None)
        if m.group(2) not in roles: roles.append(m.group(2))
def role_norm(r): return {'Decoradora':'Decorador(a)','Coordinadora':'Coordinador(a)'}.get(r,r)

# base de redondeo por rol (día vs proyecto)
per_day = {'Swings','Onset','Apoyos'}
def salbase(rn): return 100 if rn in per_day else 500

# General (todos los tipos) y por tipo
def collect(role, tipo=None):
    act, deb = [], []
    tps = [tipo] if tipo else tipos
    for tp in tps:
        key=(tp,role)
        if key in salary_map:
            ac,dc=salary_map[key]
            act+=[v for v in df[ac].apply(to_amount).dropna() if v>=1000]
            if dc: deb+=[v for v in df[dc].apply(to_amount).dropna() if v>=1000]
    return act, deb

# agrupar roles normalizados
role_groups={}
for r in roles:
    role_groups.setdefault(role_norm(r), []).append(r)

def summarize(tipo=None):
    out={}
    for rn, members in role_groups.items():
        act,deb=[],[]
        for r in members:
            a,d=collect(r,tipo); act+=a; deb+=d
        if act or deb:
            b=salbase(rn)
            out[rn]={
                'actual':rnd(np.median(act),b) if act else None, 'actual_n':len(act),
                'deseado':rnd(np.median(deb),b) if deb else None, 'deseado_n':len(deb)}
    return out

sueldos_general = summarize(None)
sueldos_por_tipo = {tp: summarize(tp) for tp in tipos}

# ---------- CONTEOS ----------
puesto_counts=df['PUESTO'].value_counts().to_dict()
total_resp=len(df)
tipo_counts={}
for lst in df['TIPOS']:
    for t in lst: tipo_counts[t]=tipo_counts.get(t,0)+1

# ---------- VIÁTICOS stats (mediana como cifra principal) ----------
def vstats(series, base=10):
    s=series.dropna()
    if len(s)==0: return None
    return {'mediana':rnd(s.median(),base),'min':int(s.min()),'max':int(s.max()),
            'moda':int(s.mode().iloc[0]) if len(s.mode()) else None,'n':int(len(s))}
vi_stats={'Comida local':vstats(df['VI_LOCAL']),'Comida fuera CDMX':vstats(df['VI_FUERA']),'Taxi':vstats(df['VI_TAXI'])}

def vi_by_puesto(col, base=10):
    d={}
    for p,v in zip(df['PUESTO'], df[col]):
        if pd.isna(p) or pd.isna(v): continue
        d.setdefault(p,[]).append(v)
    return {k:rnd(np.median(v),base) for k,v in d.items()}
vi_local_puesto=vi_by_puesto('VI_LOCAL')
vi_taxi_puesto =vi_by_puesto('VI_TAXI')

# ---------- TEMAS de respuestas abiertas ----------
TEMAS = {
 'Horas extra y jornada': ['hora','jornada','turno','tiempo extra','12 h','10 h','18-20','extensos','sabado','sábado'],
 'Plazos de pago': ['30 días','60 días','90 días','30/60','crédito','credito','plazo','no mayor a','15 dias','15 días','pago a','trabajo pagado','intereses','pagado'],
 'Viáticos y taxi': ['viatic','viátic','taxi','uber','didi','comida','alimento','traslado','gasolin','transporte','box rental','pasaje','datos móviles','internet'],
 'Sueldos y tabulador': ['sueldo','salario','tabulador','cobrar','cobre','paga','mínimo','minimo','bajos','base','cantidad'],
 'Condiciones y seguridad': ['seguro','bodega','peligros','digno','condicion','equipo','sísmic','sismic','estructural','trato','riesgo'],
 'Experiencia (senior/junior)': ['experiencia','senior','junior','años','estudios','cualidades'],
 'Agradecimiento': ['gracias'],
}
def clasifica(t):
    tl=t.lower()
    temas=[nom for nom,kws in TEMAS.items() if any(k in tl for k in kws)]
    return temas if temas else ['Otros']

abiertas=[]
for p,t in zip(df['PUESTO'], df['ABIERTA']):
    if pd.isna(t) or not str(t).strip(): continue
    txt=str(t).strip()
    abiertas.append({'puesto':(p if pd.notna(p) else 'Sin especificar'),'texto':txt,'temas':clasifica(txt)})

tema_counts={}
for a in abiertas:
    for tm in a['temas']: tema_counts[tm]=tema_counts.get(tm,0)+1

data={'total_resp':total_resp,'puesto_counts':puesto_counts,'tipo_counts':tipo_counts,
      'sueldos_general':sueldos_general,'sueldos_por_tipo':sueldos_por_tipo,
      'vi_stats':vi_stats,'vi_local_puesto':vi_local_puesto,'vi_taxi_puesto':vi_taxi_puesto,
      'pago_freq':pago_freq,'pago_by_tipo':pago_by_tipo,'abiertas':abiertas,'tema_counts':tema_counts,'n_abiertas':len(abiertas)}

# ===== PROPUESTA TABULADOR (incrustado; A=Junior / B=Senior) =====
# Para actualizarlo, reemplaza este diccionario TABULADOR con la nueva version.
TABULADOR = {'categorias': [{'nombre': 'Publicidad Nacional', 'unidad': 'diario'}, {'nombre': 'Publicidad Service', 'unidad': 'diario'}, {'nombre': 'Serie Nac', 'unidad': 'semanal'}, {'nombre': 'Serie Int', 'unidad': 'semanal'}, {'nombre': 'Película Nac', 'unidad': 'semanal'}, {'nombre': 'Película Int', 'unidad': 'semanal'}], 'puestos': [{'nombre': 'Diseñador(a)  de Producción', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 39000, 'sr': 45000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 45000, 'sr': 45000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 40000, 'sr': 45000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 40000, 'sr': 45000, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Director(a) de Arte', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 22000, 'sr': 30000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 25000, 'sr': 29000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 25000, 'sr': 30000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 25000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 45000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Set designer', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 19000, 'sr': 22000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 20000, 'sr': 23000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 20000, 'sr': 21000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 22000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 30000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Coordinador(a) Arte', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 15000, 'sr': 15000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 16000, 'sr': 17000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 15000, 'sr': 17000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 15000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 20000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Asistente de coordinación', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 9000, 'sr': 10000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 10000, 'sr': 12000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 8000, 'sr': 11000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 7500, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 13000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Decorador(a)', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 20000, 'sr': 22000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 22000, 'sr': 23000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 20000, 'sr': 22000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 20000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 30000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': '1er Asistente de decoración', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 12000, 'sr': 14000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 13000, 'sr': 15000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 13000, 'sr': 15000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 12000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 15000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': '2do Asistente de decoración', 'pendiente': True, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Comprador', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 12000, 'sr': 15000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 12000, 'sr': 15000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 9000, 'sr': 12000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 13000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Prop Master', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 12000, 'sr': 15000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 14000, 'sr': 17000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 15000, 'sr': 19000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 15000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 25000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Asistente de Prop', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 10000, 'sr': 12000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 10000, 'sr': 12000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 11000, 'sr': 14000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 9000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 15000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Diseñador(a)  Gráfico', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 10000, 'sr': 15000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 12000, 'sr': 15000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 13000, 'sr': 16000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 12000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 13000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Asistente de Diseñador Gráfico', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 12000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Onset', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 3500, 'sr': 3800, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 2500, 'sr': 4000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 15000, 'sr': 18000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 12000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'On set Bilingüe', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 4000, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 13000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Asistente On set', 'pendiente': True, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Leadman', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 3000, 'sr': 3000, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 3000, 'sr': 3000, 'unidad': 'diario'}, 'Serie Nac': {'jr': 18000, 'sr': 18000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 14000, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Swings', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 1800, 'sr': 2500, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 2000, 'sr': 2500, 'unidad': 'diario'}, 'Serie Nac': {'jr': 8000, 'sr': 12000, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 9000, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 9900, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Apoyos', 'pendiente': False, 'valores': {'Publicidad Nacional': {'jr': 1200, 'sr': 1200, 'unidad': 'diario'}, 'Publicidad Service': {'jr': 1100, 'sr': 1500, 'unidad': 'diario'}, 'Serie Nac': {'jr': 6000, 'sr': 7200, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': 7200, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': 7200, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Carpinteros', 'pendiente': True, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Pintores', 'pendiente': True, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Leadman Construcciòn', 'pendiente': True, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Comprador Construcciòn', 'pendiente': True, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}, {'nombre': 'Coordinador(a) Construcciòn', 'pendiente': True, 'valores': {'Publicidad Nacional': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Publicidad Service': {'jr': None, 'sr': None, 'unidad': 'diario'}, 'Serie Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Serie Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Nac': {'jr': None, 'sr': None, 'unidad': 'semanal'}, 'Película Int': {'jr': None, 'sr': None, 'unidad': 'semanal'}}}]}
data['tabulador'] = TABULADOR

import json
d = data
DATA = json.dumps(d, ensure_ascii=False)

html = r'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard · Sueldos y Tabulador — Departamento de Arte</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#0f1117; --panel:#181b24; --panel2:#1f2330; --ink:#e8eaf0; --muted:#9aa0b4;
    --line:#2a2f3d; --accent:#ff6b9d; --accent2:#5ed0ff; --accent3:#ffd166; --green:#4ade80; --red:#f87171;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5}
  header{padding:28px 28px 10px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:22px;font-weight:700}
  .sub{color:var(--muted);font-size:13px;margin-top:4px}
  .tabs{display:flex;gap:6px;padding:14px 28px 0;flex-wrap:wrap}
  .tab{padding:9px 18px;border-radius:9px 9px 0 0;background:var(--panel);color:var(--muted);cursor:pointer;font-size:14px;font-weight:600;border:1px solid var(--line);border-bottom:none}
  .tab.active{background:var(--panel2);color:var(--ink)}
  main{padding:22px 28px 60px}
  .page{display:none} .page.active{display:block}
  .grid{display:grid;gap:16px}
  .kpis{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
  .kpi .n{font-size:34px;font-weight:800;letter-spacing:-.5px}
  .kpi .l{color:var(--muted);font-size:12.5px;margin-top:2px}
  .kpi .n.pink{color:var(--accent)} .kpi .n.blue{color:var(--accent2)} .kpi .n.yellow{color:var(--accent3)} .kpi .n.green{color:var(--green)}
  h2{font-size:15px;margin:0 0 14px;font-weight:700}
  .sec-title{font-size:13px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;color:var(--accent2);margin:26px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
  .sec-title.pink{color:var(--accent)}
  .two{grid-template-columns:1.4fr 1fr}
  .three{grid-template-columns:repeat(3,1fr)}
  @media(max-width:880px){.two,.three{grid-template-columns:1fr}}
  .chart-wrap{position:relative;height:340px}
  .chart-wrap.tall{height:430px}
  .pies{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
  .pie-cell{text-align:center}
  .pie-cell .pt{font-size:11.5px;color:var(--muted);font-weight:700;margin-bottom:4px}
  .pie-wrap{position:relative;height:150px}
  @media(max-width:560px){.pies{grid-template-columns:1fr}}
  .up{color:var(--green)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.3px}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  td.puesto{font-weight:600}
  .pend{color:var(--muted);font-style:italic}
  .jr{color:var(--accent2)} .sr{color:var(--accent)}
  .badge{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:20px;padding:2px 10px;font-size:11px;color:var(--muted);margin-left:6px}
  .quote{background:var(--panel2);border-left:3px solid var(--accent);border-radius:8px;padding:12px 14px;margin-bottom:10px}
  .quote .p{font-size:11.5px;color:var(--accent2);font-weight:700;text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .quote .tags{color:var(--muted);font-weight:600}
  .quote .tag{display:inline-block;background:#232838;border:1px solid var(--line);border-radius:20px;padding:1px 8px;margin-left:4px;color:var(--accent3);text-transform:none;letter-spacing:0}
  .quote .t{font-size:13.5px;white-space:pre-line}
  .filters{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
  label.fl{color:var(--muted);font-size:13px;margin-right:8px}
  select{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:13px}
  .note{color:var(--muted);font-size:12px;margin-top:10px}
  .legend-row{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
  .dot{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:middle}
  .toggle{display:inline-flex;gap:4px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:4px}
  .toggle button{background:transparent;border:none;color:var(--muted);font-size:12.5px;font-weight:600;padding:6px 12px;border-radius:7px;cursor:pointer}
  .toggle button.active{background:var(--accent);color:#12131a}
  .cnt{color:var(--muted);font-size:12px;margin-bottom:10px}
</style>
</head>
<body>
<header>
  <h1>Sueldos y Tabulador — Departamento de Arte</h1>
  <div class="sub">Resultados de la encuesta · <b id="hTotal"></b> respuestas · Cifras = <b>valor típico (mediana)</b>, redondeado · Julio 2026</div>
</header>
<div class="tabs">
  <div class="tab active" data-p="dash">Dashboard general</div>
  <div class="tab" data-p="viaticos">Análisis de viáticos</div>
  <div class="tab" data-p="abiertas">Respuestas abiertas</div>
  <div class="tab" data-p="tabulador">Propuesta tabulador</div>
</div>
<main>

<!-- ================= HOJA 1 ================= -->
<section class="page active" id="dash">
  <div class="grid kpis" style="margin-bottom:16px">
    <div class="card kpi"><div class="n pink" id="kTotal"></div><div class="l">Respuestas totales</div></div>
    <div class="card kpi"><div class="n blue" id="kPuestos"></div><div class="l">Puestos representados</div></div>
  </div>

  <div class="grid two" style="margin-bottom:16px">
    <div class="card">
      <h2>Muestra por tipo de puesto</h2>
      <div class="chart-wrap tall"><canvas id="chPuesto"></canvas></div>
    </div>
    <div class="card">
      <h2>Tipo de producción trabajada <span class="badge">respuesta múltiple</span></h2>
      <div class="chart-wrap"><canvas id="chTipo"></canvas></div>
      <div class="note">Cada persona pudo marcar más de un tipo de producción.</div>
    </div>
  </div>

  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:6px">
      <h2 style="margin:0">Sueldo actual vs. propuesto por puesto (MXN)</h2>
      <div class="toggle" id="tgSueldo">
        <button class="active" data-t="General">General</button>
        <button data-t="Publicidad Nacional">Pub. Nacional</button>
        <button data-t="Publicidad Service">Pub. Service</button>
        <button data-t="Serie o Película">Serie/Película</button>
      </div>
    </div>
    <div class="chart-wrap tall"><canvas id="chSueldo"></canvas></div>
    <div class="legend-row">
      <span><span class="dot" style="background:#5ed0ff"></span>Actual (mediana)</span>
      <span><span class="dot" style="background:#ff6b9d"></span>Propuesto / tabulador (mediana)</span>
    </div>
    <div class="note" id="noteSueldo"></div>
  </div>
</section>

<!-- ================= HOJA 2 ================= -->
<section class="page" id="viaticos">

  <div class="sec-title">🍽️ Viáticos de comida</div>
  <div class="grid kpis" style="margin-bottom:16px">
    <div class="card kpi"><div class="n blue" id="vLocal"></div><div class="l">Comida LOCAL · valor típico/día</div></div>
    <div class="card kpi"><div class="n yellow" id="vFuera"></div><div class="l">Comida FUERA de CDMX · valor típico/día</div></div>
  </div>
  <div class="grid two" style="margin-bottom:8px">
    <div class="card">
      <h2>Monto propuesto de comida (MXN/día)</h2>
      <div class="chart-wrap"><canvas id="chComidaMonto"></canvas></div>
      <div class="legend-row">
        <span><span class="dot" style="background:#9aa0b4"></span>Mínimo</span>
        <span><span class="dot" style="background:#ffd166"></span>Mediana</span>
        <span><span class="dot" style="background:#ff6b9d"></span>Máximo</span>
      </div>
    </div>
    <div class="card">
      <h2>¿Te han pagado la comida en proyectos recientes? <span class="badge">por tipo de producción</span></h2>
      <div class="pies">
        <div class="pie-cell"><div class="pt">Publicidad Nacional</div><div class="pie-wrap"><canvas id="chComidaPagoPN"></canvas></div></div>
        <div class="pie-cell"><div class="pt">Publicidad Service</div><div class="pie-wrap"><canvas id="chComidaPagoSV"></canvas></div></div>
        <div class="pie-cell"><div class="pt">Serie o Película</div><div class="pie-wrap"><canvas id="chComidaPagoSP"></canvas></div></div>
      </div>
      <div class="legend-row" style="justify-content:center">
        <span><span class="dot" style="background:#4ade80"></span>Sí, siempre</span>
        <span><span class="dot" style="background:#5ed0ff"></span>Casi siempre</span>
        <span><span class="dot" style="background:#ffd166"></span>A veces</span>
        <span><span class="dot" style="background:#f87171"></span>Nunca</span>
      </div>
      <div class="note">Incluye comida local y fuera de CDMX. Cada pastel = una etapa de producción.</div>
    </div>
  </div>
  <div class="grid two">
    <div class="card">
      <h2>Comida local propuesta por puesto (MXN/día)</h2>
      <div class="chart-wrap tall"><canvas id="chComidaPuesto"></canvas></div>
    </div>
    <div class="card"><h2>Lectura rápida — comida</h2><div id="notasComida"></div></div>
  </div>

  <div class="sec-title pink">🚕 Apoyo para taxi</div>
  <div class="grid kpis" style="margin-bottom:16px">
    <div class="card kpi"><div class="n pink" id="vTaxi"></div><div class="l">Taxi · valor típico/día</div></div>
    <div class="card kpi"><div class="n red" id="vTaxiNunca"></div><div class="l">Dice que NUNCA se lo pagan</div></div>
  </div>
  <div class="grid two" style="margin-bottom:8px">
    <div class="card">
      <h2>Monto propuesto de taxi (MXN/día)</h2>
      <div class="chart-wrap"><canvas id="chTaxiMonto"></canvas></div>
      <div class="legend-row">
        <span><span class="dot" style="background:#9aa0b4"></span>Mínimo</span>
        <span><span class="dot" style="background:#ffd166"></span>Mediana</span>
        <span><span class="dot" style="background:#ff6b9d"></span>Máximo</span>
      </div>
    </div>
    <div class="card">
      <h2>¿Te han pagado el taxi en proyectos recientes? <span class="badge">por tipo de producción</span></h2>
      <div class="pies">
        <div class="pie-cell"><div class="pt">Publicidad Nacional</div><div class="pie-wrap"><canvas id="chTaxiPagoPN"></canvas></div></div>
        <div class="pie-cell"><div class="pt">Publicidad Service</div><div class="pie-wrap"><canvas id="chTaxiPagoSV"></canvas></div></div>
        <div class="pie-cell"><div class="pt">Serie o Película</div><div class="pie-wrap"><canvas id="chTaxiPagoSP"></canvas></div></div>
      </div>
      <div class="legend-row" style="justify-content:center">
        <span><span class="dot" style="background:#4ade80"></span>Sí, siempre</span>
        <span><span class="dot" style="background:#5ed0ff"></span>Casi siempre</span>
        <span><span class="dot" style="background:#ffd166"></span>A veces</span>
        <span><span class="dot" style="background:#f87171"></span>Nunca</span>
      </div>
      <div class="note">Cada pastel = una etapa de producción.</div>
    </div>
  </div>
  <div class="grid two">
    <div class="card">
      <h2>Taxi propuesto por puesto (MXN/día)</h2>
      <div class="chart-wrap tall"><canvas id="chTaxiPuesto"></canvas></div>
    </div>
    <div class="card"><h2>Lectura rápida — taxi</h2><div id="notasTaxi"></div></div>
  </div>
</section>

<!-- ================= HOJA 3 ================= -->
<section class="page" id="abiertas">
  <div class="card">
    <div class="filters">
      <div><label class="fl">Puesto:</label>
        <select id="fPuesto"><option value="__all">Todos los puestos</option></select></div>
      <div><label class="fl">Tema:</label>
        <select id="fTema"><option value="__all">Todos los temas</option></select></div>
    </div>
    <div class="cnt" id="qCount"></div>
    <div id="quotes"></div>
  </div>
</section>

<!-- ===== HOJA 4: PROPUESTA TABULADOR ===== -->
<section class="page" id="tabulador">
  <div class="note" style="margin-bottom:14px">Propuesta de tabulador por puesto, con dos niveles: <b class="jr">Junior</b> (base) y <b class="sr">Senior</b> (con experiencia). Publicidad se paga <b>por día</b>; Serie y Película <b>por semana</b>. Los puestos marcados <span class="pend">por definir</span> aún no tienen cifras.</div>
  <div class="card" style="margin-bottom:16px">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:6px">
      <h2 style="margin:0">Comparativo Junior vs. Senior</h2>
      <div><label class="fl">Categoría:</label><select id="fCat"></select></div>
    </div>
    <div class="chart-wrap tall"><canvas id="chTab"></canvas></div>
    <div class="legend-row"><span><span class="dot" style="background:#5ed0ff"></span>Junior</span><span><span class="dot" style="background:#ff6b9d"></span>Senior</span></div>
    <div class="note" id="tabUnidad"></div>
  </div>
  <div class="card" style="margin-bottom:16px">
    <h2>Sueldo diario — Publicidad (MXN/día)</h2>
    <div style="overflow-x:auto"><table id="tblDiario"></table></div>
  </div>
  <div class="card">
    <h2>Sueldo semanal — Serie y Película (MXN/semana)</h2>
    <div style="overflow-x:auto"><table id="tblSemanal"></table></div>
  </div>
</section>

</main>
<script>
const D = __DATA__;
document.getElementById('hTotal').textContent = D.total_resp;
const money = n => n==null? '—' : '$'+Number(n).toLocaleString('es-MX');
Chart.defaults.color='#9aa0b4'; Chart.defaults.font.family="-apple-system,Segoe UI,Roboto,sans-serif";
const GRID='#2a2f3d';

document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
  t.classList.add('active'); document.getElementById(t.dataset.p).classList.add('active');
}));

// ===== HOJA 1 KPIs =====
document.getElementById('kTotal').textContent=D.total_resp;
document.getElementById('kPuestos').textContent=Object.keys(D.puesto_counts).length;

// Muestra por puesto
const pE=Object.entries(D.puesto_counts).sort((a,b)=>b[1]-a[1]);
new Chart(document.getElementById('chPuesto'),{type:'bar',
  data:{labels:pE.map(x=>x[0]),datasets:[{data:pE.map(x=>x[1]),backgroundColor:'#ff6b9d',borderRadius:6}]},
  options:{indexAxis:'y',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.raw+' respuestas ('+(100*c.raw/D.total_resp).toFixed(0)+'%)'}}},
    scales:{x:{grid:{color:GRID},ticks:{precision:0}},y:{grid:{display:false}}}}});

// Tipo de produccion
const tE=Object.entries(D.tipo_counts).sort((a,b)=>b[1]-a[1]);
new Chart(document.getElementById('chTipo'),{type:'bar',
  data:{labels:tE.map(x=>x[0]),datasets:[{data:tE.map(x=>x[1]),backgroundColor:['#5ed0ff','#ffd166','#ff6b9d'],borderRadius:6}]},
  options:{plugins:{legend:{display:false}},scales:{y:{grid:{color:GRID},ticks:{precision:0}},x:{grid:{display:false}}}}});

// Sueldo con toggle
let chSueldo;
function drawSueldo(sel){
  const src = sel==='General'? D.sueldos_general : D.sueldos_por_tipo[sel];
  const e=Object.entries(src).filter(x=>x[1].deseado!=null||x[1].actual!=null)
          .sort((a,b)=>(b[1].deseado||b[1].actual)-(a[1].deseado||a[1].actual));
  const labels=e.map(x=>x[0]);
  const dsAct=e.map(x=>x[1].actual), dsDes=e.map(x=>x[1].deseado);
  const notes={
    'General':'⚠️ La vista <b>General</b> mezcla unidades: en <b>Publicidad Nacional</b> y <b>Publicidad Service</b> los sueldos son <b>por día de filmación</b>, mientras que en <b>Serie/Película</b> son <b>por semana</b>. Para comparar de forma justa, usa las pestañas por tipo de producción.',
    'Publicidad Nacional':'Cifra = mediana redondeada · montos <b>por día de filmación</b>.',
    'Publicidad Service':'Cifra = mediana redondeada · montos <b>por día de filmación</b>.',
    'Serie o Película':'Cifra = mediana redondeada · montos <b>por semana</b>.'};
  document.getElementById('noteSueldo').innerHTML=notes[sel];
  const unit = sel==='Serie o Película' ? '/semana' : (sel==='General' ? '' : '/día');
  if(chSueldo) chSueldo.destroy();
  chSueldo=new Chart(document.getElementById('chSueldo'),{type:'bar',
    data:{labels,datasets:[
      {label:'Actual',data:dsAct,backgroundColor:'#5ed0ff',borderRadius:5},
      {label:'Propuesto',data:dsDes,backgroundColor:'#ff6b9d',borderRadius:5}]},
    options:{indexAxis:'y',plugins:{legend:{display:false},
      tooltip:{callbacks:{label:c=>c.dataset.label+': '+money(c.raw)+unit}}},
      scales:{x:{grid:{color:GRID},ticks:{callback:v=>'$'+(v/1000)+'k'}},y:{grid:{display:false}}}}});
}
drawSueldo('General');
document.querySelectorAll('#tgSueldo button').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('#tgSueldo button').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); drawSueldo(b.dataset.t);
}));

// ===== HOJA 2 =====
const fo=['Sí, siempre','Casi siempre','A veces','Nunca'];
const fcol={'Sí, siempre':'#4ade80','Casi siempre':'#5ed0ff','A veces':'#ffd166','Nunca':'#f87171'};
const sum=o=>Object.values(o).reduce((a,b)=>a+b,0);
const pct=(o,k)=>Math.round(100*o[k]/sum(o));

// --- Comida ---
document.getElementById('vLocal').textContent=money(D.vi_stats['Comida local'].mediana);
document.getElementById('vFuera').textContent=money(D.vi_stats['Comida fuera CDMX'].mediana);
const ck=['Comida local','Comida fuera CDMX'];
new Chart(document.getElementById('chComidaMonto'),{type:'bar',
  data:{labels:ck,datasets:[
    {label:'Mínimo',data:ck.map(k=>D.vi_stats[k].min),backgroundColor:'#9aa0b4',borderRadius:4},
    {label:'Mediana',data:ck.map(k=>D.vi_stats[k].mediana),backgroundColor:'#ffd166',borderRadius:4},
    {label:'Máximo',data:ck.map(k=>D.vi_stats[k].max),backgroundColor:'#ff6b9d',borderRadius:4}]},
  options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label+': '+money(c.raw)}}},
    scales:{y:{grid:{color:GRID},ticks:{callback:v=>'$'+v}},x:{grid:{display:false}}}}});
// pastel de pago por tipo de producción
const foP=['Sí, siempre','Casi siempre','A veces','Nunca'];
const fcolP=['#4ade80','#5ed0ff','#ffd166','#f87171'];
function drawPie(id,obj){
  const data=foP.map(f=>obj[f]); const tot=data.reduce((a,b)=>a+b,0)||1;
  new Chart(document.getElementById(id),{type:'doughnut',
    data:{labels:foP,datasets:[{data,backgroundColor:fcolP,borderColor:'#181b24',borderWidth:2}]},
    options:{cutout:'52%',plugins:{legend:{display:false},
      tooltip:{callbacks:{label:c=>c.label+': '+c.raw+' ('+Math.round(100*c.raw/tot)+'%)'}}}}});
}
drawPie('chComidaPagoPN',D.pago_by_tipo['Publicidad Nacional'].comida);
drawPie('chComidaPagoSV',D.pago_by_tipo['Publicidad Service'].comida);
drawPie('chComidaPagoSP',D.pago_by_tipo['Serie o Película'].comida);
const clE=Object.entries(D.vi_local_puesto).sort((a,b)=>b[1]-a[1]);
new Chart(document.getElementById('chComidaPuesto'),{type:'bar',
  data:{labels:clE.map(x=>x[0]),datasets:[{data:clE.map(x=>x[1]),backgroundColor:'#ffd166',borderRadius:5}]},
  options:{indexAxis:'y',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+'/día'}}},
    scales:{x:{grid:{color:GRID},ticks:{callback:v=>'$'+v}},y:{grid:{display:false}}}}});
const pl=D.pago_freq['Viático x comida (local)'], pf=D.pago_freq['Viático x comida (fuera de CDMX)'];
document.getElementById('notasComida').innerHTML=`
  <div class="quote"><div class="t">El consenso para <b>comida local</b> es <b>${money(D.vi_stats['Comida local'].mediana)}/día</b>, subiendo a <b>${money(D.vi_stats['Comida fuera CDMX'].mediana)}</b> cuando el rodaje es fuera de CDMX.</div></div>
  <div class="quote"><div class="t">La comida <b>fuera de CDMX</b> es de los viáticos mejor cubiertos: <b>${pct(pf,'Sí, siempre')}%</b> la recibe siempre.</div></div>
  <div class="quote"><div class="t">Aun así, <b>${pct(pl,'Nunca')}%</b> dice que la comida local <b>nunca</b> se la pagan.</div></div>
  <div class="quote"><div class="t">Puestos de oficina/bodega (<b>Diseñador Gráfico</b>, <b>Director de Arte</b>) piden los montos más altos por traslados largos con equipo.</div></div>`;

// --- Taxi ---
document.getElementById('vTaxi').textContent=money(D.vi_stats['Taxi'].mediana);
const tx=D.pago_freq['Apoyo para taxi'];
document.getElementById('vTaxiNunca').textContent=pct(tx,'Nunca')+'%';
new Chart(document.getElementById('chTaxiMonto'),{type:'bar',
  data:{labels:['Apoyo taxi'],datasets:[
    {label:'Mínimo',data:[D.vi_stats['Taxi'].min],backgroundColor:'#9aa0b4',borderRadius:4},
    {label:'Mediana',data:[D.vi_stats['Taxi'].mediana],backgroundColor:'#ffd166',borderRadius:4},
    {label:'Máximo',data:[D.vi_stats['Taxi'].max],backgroundColor:'#ff6b9d',borderRadius:4}]},
  options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label+': '+money(c.raw)}}},
    scales:{y:{grid:{color:GRID},ticks:{callback:v=>'$'+v}},x:{grid:{display:false}}}}});
drawPie('chTaxiPagoPN',D.pago_by_tipo['Publicidad Nacional'].taxi);
drawPie('chTaxiPagoSV',D.pago_by_tipo['Publicidad Service'].taxi);
drawPie('chTaxiPagoSP',D.pago_by_tipo['Serie o Película'].taxi);
const txE=Object.entries(D.vi_taxi_puesto).sort((a,b)=>b[1]-a[1]);
new Chart(document.getElementById('chTaxiPuesto'),{type:'bar',
  data:{labels:txE.map(x=>x[0]),datasets:[{data:txE.map(x=>x[1]),backgroundColor:'#ff6b9d',borderRadius:5}]},
  options:{indexAxis:'y',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+'/día'}}},
    scales:{x:{grid:{color:GRID},ticks:{callback:v=>'$'+v}},y:{grid:{display:false}}}}});
document.getElementById('notasTaxi').innerHTML=`
  <div class="quote"><div class="t">El apoyo de taxi propuesto ronda los <b>${money(D.vi_stats['Taxi'].mediana)}/día</b>, aunque varios piden que se pague <b>lo que marque el Uber/Didi</b>.</div></div>
  <div class="quote"><div class="t">Es el viático <b>más incumplido</b>: <b>${pct(tx,'Nunca')}%</b> dice que <b>nunca</b> se lo pagan y solo <b>${pct(tx,'Sí, siempre')}%</b> lo recibe siempre.</div></div>
  <div class="quote"><div class="t">Los <b>Swings</b> insisten en que viven lejos del centro y hoy pagan el traslado de su bolsillo.</div></div>`;

// ===== HOJA 3 =====
const selP=document.getElementById('fPuesto'), selT=document.getElementById('fTema');
[...new Set(D.abiertas.map(a=>a.puesto))].sort().forEach(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;selP.appendChild(o);});
Object.entries(D.tema_counts).sort((a,b)=>b[1]-a[1]).forEach(([t,n])=>{const o=document.createElement('option');o.value=t;o.textContent=t+' ('+n+')';selT.appendChild(o);});
function render(){
  const fp=selP.value, ft=selT.value;
  const list=D.abiertas.filter(a=>(fp==='__all'||a.puesto===fp)&&(ft==='__all'||a.temas.includes(ft)));
  document.getElementById('qCount').textContent=list.length+' de '+D.abiertas.length+' comentarios';
  const box=document.getElementById('quotes');box.innerHTML='';
  list.forEach(a=>{
    const d=document.createElement('div');d.className='quote';
    const tags=a.temas.map(t=>'<span class="tag">'+t+'</span>').join('');
    d.innerHTML='<div class="p">'+a.puesto+' <span class="tags">'+tags+'</span></div><div class="t"></div>';
    d.querySelector('.t').textContent=a.texto;box.appendChild(d);
  });
}
selP.addEventListener('change',render); selT.addEventListener('change',render); render();

// ===== HOJA 4 TABULADOR =====
const TAB=D.tabulador;const cats=TAB.categorias.map(c=>c.nombre);
const catUnidad=Object.fromEntries(TAB.categorias.map(c=>[c.nombre,c.unidad]));
const fCat=document.getElementById('fCat');
cats.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;fCat.appendChild(o);});
let chTab;
function drawTab(cat){
  const rows=TAB.puestos.filter(p=>p.valores[cat]&&(p.valores[cat].jr!=null||p.valores[cat].sr!=null));
  const u=catUnidad[cat]==='diario'?'/día':'/semana';
  document.getElementById('tabUnidad').innerHTML='Categoría <b>'+cat+'</b> · '+(catUnidad[cat]==='diario'?'sueldo por día':'sueldo por semana')+'. Solo se grafican puestos con cifras.';
  if(chTab)chTab.destroy();
  chTab=new Chart(document.getElementById('chTab'),{type:'bar',
    data:{labels:rows.map(p=>p.nombre),datasets:[
      {label:'Junior',data:rows.map(p=>p.valores[cat].jr),backgroundColor:'#5ed0ff',borderRadius:5},
      {label:'Senior',data:rows.map(p=>p.valores[cat].sr),backgroundColor:'#ff6b9d',borderRadius:5}]},
    options:{indexAxis:'y',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label+': '+money(c.raw)+u}}},
      scales:{x:{grid:{color:GRID},ticks:{callback:v=>'$'+(v/1000)+'k'}},y:{grid:{display:false}}}}});
}
drawTab(cats[0]);
fCat.addEventListener('change',()=>drawTab(fCat.value));
function cellT(v,cls){return v==null?'<td class="num pend">—</td>':'<td class="num '+cls+'">'+money(v)+'</td>';}
function buildTable(id,catList){
  const el=document.getElementById(id);
  let h='<thead><tr><th>Puesto</th>';
  catList.forEach(c=>{h+='<th class="jr" style="text-align:right">'+c+' · Jr</th><th class="sr" style="text-align:right">'+c+' · Sr</th>';});
  h+='</tr></thead><tbody>';
  TAB.puestos.forEach(p=>{
    if(p.pendiente){h+='<tr><td class="puesto">'+p.nombre+'</td><td class="pend" colspan="'+(catList.length*2)+'">Por definir</td></tr>';return;}
    h+='<tr><td class="puesto">'+p.nombre+'</td>';
    catList.forEach(c=>{const v=p.valores[c]||{};h+=cellT(v.jr,'jr')+cellT(v.sr,'sr');});
    h+='</tr>';
  });
  h+='</tbody>';el.innerHTML=h;
}
buildTable('tblDiario',['Publicidad Nacional','Publicidad Service']);
buildTable('tblSemanal',['Serie Nac','Serie Int','Película Nac','Película Int']);
</script>
</body>
</html>'''

html = html.replace('__DATA__', DATA)
OUT = os.environ.get('OUT_PATH','index.html')
open(OUT,'w',encoding='utf-8').write(html)
print('index.html generado:', len(html), 'bytes,', html.count('<canvas'), 'graficas')
