# Total Security · Sitio de prueba

Sitio de **Total Security** (seguridad electrónica en Nuevo León) con:
servicios, nosotros (misión y visión), proyectos, blog, contacto y la **tienda con el catálogo completo de Syscom**.

## Cómo se actualiza la tienda
Un proceso automático de GitHub (`.github/workflows/actualizar.yml`) corre `sync_tienda.py`
a las **9 am, 12 pm, 3 pm y 6 pm (hora de Monterrey), de lunes a sábado**. Descarga de Syscom
los productos, fotos, características y existencias, y vuelve a publicar la página.

- **No se publican precios.** Los clientes arman su lista y la envían por WhatsApp o correo.
- Las claves de Syscom viven en **Settings → Secrets and variables → Actions** con los nombres
  `SYSCOM_CLIENT_ID` y `SYSCOM_CLIENT_SECRET`. Nunca se escriben en los archivos.
- Si Syscom falla durante una actualización, la página se queda con los datos de la vez anterior.
- Para actualizar en cualquier momento: pestaña **Actions → Actualizar catálogo y publicar → Run workflow**.
