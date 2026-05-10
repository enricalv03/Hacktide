import { useCallback, useEffect, useState } from "react";

/** Cola de llamadas curada por el usuario (persistida en localStorage).
 *
 *  Cada item: `{ clienteId: string, addedAt: ISO date, status: 'pendiente' | 'contactado' | 'recuperado' | 'perdido', notes?: string }`
 *
 *  El usuario añade clientes con el botón «Enviar a Alertas y Contacto» desde
 *  Análisis o Resumen ejecutivo y luego los gestiona en la pestaña Alertas y
 *  Contacto. La cola es local del navegador — no toca aún la DB. Si en el
 *  futuro se quiere sincronizar con `seguimiento_alerta`, basta con disparar
 *  `postAlertOutcome` desde `markStatus`.
 */
const STORAGE_KEY = "inibsa_call_queue_v1";
const QUEUE_EVENT = "inibsa-call-queue-change";

const VALID_STATUSES = ["pendiente", "contactado", "recuperado", "perdido"];

function readStorage() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (it) => it && typeof it.clienteId === "string" && VALID_STATUSES.includes(it.status),
    );
  } catch {
    return [];
  }
}

function writeStorage(items) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    /* quota / private mode: ignoramos silenciosamente */
  }
  // Notifica a otros componentes en la misma pestaña (storage event nativo
  // solo se dispara entre pestañas distintas).
  window.dispatchEvent(new CustomEvent(QUEUE_EVENT));
}

export function useCallQueue() {
  const [items, setItems] = useState(() => (typeof window !== "undefined" ? readStorage() : []));

  // Sincroniza si otro componente o pestaña actualiza la cola.
  useEffect(() => {
    const sync = () => setItems(readStorage());
    window.addEventListener(QUEUE_EVENT, sync);
    window.addEventListener("storage", (e) => {
      if (e.key === STORAGE_KEY) sync();
    });
    return () => {
      window.removeEventListener(QUEUE_EVENT, sync);
    };
  }, []);

  const add = useCallback((clienteId) => {
    if (!clienteId) return;
    const cid = String(clienteId);
    const current = readStorage();
    const exists = current.some((it) => it.clienteId === cid);
    const next = exists
      ? current.map((it) =>
          it.clienteId === cid ? { ...it, status: "pendiente", addedAt: new Date().toISOString() } : it,
        )
      : [
          { clienteId: cid, addedAt: new Date().toISOString(), status: "pendiente" },
          ...current,
        ];
    writeStorage(next);
    setItems(next);
  }, []);

  const remove = useCallback((clienteId) => {
    const cid = String(clienteId);
    const next = readStorage().filter((it) => it.clienteId !== cid);
    writeStorage(next);
    setItems(next);
  }, []);

  const markStatus = useCallback((clienteId, status, notes) => {
    if (!VALID_STATUSES.includes(status)) return;
    const cid = String(clienteId);
    const current = readStorage();
    const next = current.map((it) =>
      it.clienteId === cid ? { ...it, status, notes: notes ?? it.notes } : it,
    );
    writeStorage(next);
    setItems(next);
  }, []);

  const clearAll = useCallback(() => {
    writeStorage([]);
    setItems([]);
  }, []);

  return { items, add, remove, markStatus, clearAll };
}
