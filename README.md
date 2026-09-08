# S&S Group · Control de Cuentas ONLINE

Versión preparada para Streamlit Community Cloud + Supabase.

Datos iniciales incluidos:
- 42 clientes/cuentas
- 73 movimientos
- 38 registros de honorarios

## Archivos
- `app.py`: aplicación online
- `requirements.txt`: dependencias para Streamlit
- `01_schema.sql`: crea tablas y bucket de facturas en Supabase
- `02_datos_iniciales.sql`: carga la base actual migrada desde el Excel
- `.streamlit/secrets.toml.example`: ejemplo de secretos

## Importante
No subas un archivo real `secrets.toml` a GitHub.
En Streamlit Cloud los secretos se cargan desde Settings > Secrets.
