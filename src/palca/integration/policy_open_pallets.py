"""Palanca operativa bound: N posiciones físicas por destino, scorer de producción.

El DES conserva una sola cola por flujo. Solo se amplía el conjunto de palets
candidatos; no se duplica ni reordena la ventana antes de puntuar.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from .kpi_hooks import aggregate_pallet_kpis
from .policy_packer_sched import PolicyPackerScheduler


class OpenPalletScheduler(PolicyPackerScheduler):
    manages_open_pallets = True

    def __init__(self, config, *, open_pallets: int, t_changeover: float):
        super().__init__(config)
        if open_pallets < 2:
            raise ValueError("OpenPalletScheduler requiere open_pallets >= 2")
        if config.micro_plan_enabled or config.online_controller:
            raise ValueError("open_pallets > 1 requiere micro_plan y online_controller desactivados")
        self.open_pallets = open_pallets
        self.t_changeover = t_changeover
        self._choices: dict[int, list[str]] = {}
        self._slot_dest: dict[str, int] = {}
        self._ready_at: dict[str, float] = {}
        self._records: dict[int, list[dict]] = {}
        self._slot_generation: dict[str, int] = {}
        self._last_commit: dict[str, float] = {}
        self._slot_closures = 0
        self._retry_at = None

    def _collect_pallets(self, ramps):
        for ramp in ramps.values():
            for box in ramp.queue:
                dest = box.destination
                if dest not in self._choices:
                    slots = [f"{dest}:{i + 1}" for i in range(self.open_pallets)]
                    self._choices[dest] = slots
                    for slot in slots:
                        self._slot_dest[slot] = dest
                        self._slot_generation[slot] = 1
                        self._pallets[slot] = self._new_pallet()
        return dict(self._pallets)

    def _run_scheduler_attempt(self, *, sim_state, overrides):
        self._retry_at = None
        blocked = {slot for slot, dest in self._slot_dest.items()
                   if dest in sim_state.pallet_blocked or self._ready_at.get(slot, 0) > sim_state.now}
        state = replace(sim_state, pallet_choices=self._choices, pallet_blocked=blocked)
        plan, fail, _, stop, details = super()._run_scheduler_attempt(sim_state=state, overrides=overrides)
        stats = self._scheduler.last_eval_stats
        closed = False
        # Nunca inferir NO_VISIBLE_FIT de una ventana examinada parcialmente.
        if not stats.get("cutoff", False):
            feasible = stats.get("feasible_by_pallet", {})
            for slot in stats.get("evaluated_by_pallet", {}):
                if not feasible.get(slot) and self._pallets[slot].placements:
                    self._close_slot(slot, "NO_VISIBLE_FIT", sim_state.now)
                    self._pallets[slot] = self._new_pallet()
                    self._ready_at[slot] = sim_state.now + self.t_changeover
                    self._slot_generation[slot] += 1
                    self._slot_closures += 1
                    self._retry_at = sim_state.now + self.t_changeover
                    closed = True
        # El DES espera el cambio de posición, sin cerrar todos los palets del destino.
        if closed or self.next_wakeup(sim_state.now) is not None:
            stop, details = None, {}
        return plan, fail, {}, stop, details

    def next_wakeup(self, now):
        times = [time for time in self._ready_at.values() if time > now]
        if self._retry_at is not None:
            times.append(self._retry_at)
        return min(times) if times else None

    def commit_plan(self, plan, time=None):
        before = len(self._pallets[plan.pallet_id].placements)
        super().commit_plan(plan, time)
        if len(self._pallets[plan.pallet_id].placements) != before + 1:
            raise RuntimeError(f"No se pudo confirmar la colocación en {plan.pallet_id}")
        self._last_commit[plan.pallet_id] = float(time or 0)

    def _close_slot(self, slot, reason, now):
        pallet = self._pallets.pop(slot)
        if not pallet.placements:
            return
        dest = self._slot_dest[slot]
        items = self._committed_placements.pop(slot, [])
        if len(items) != len(pallet.placements):
            raise RuntimeError("Dump incompleto de palet físico")
        for item in items:
            item.update(pallet_id=dest, physical_slot=slot,
                        slot_generation=self._slot_generation[slot])
        self._records.setdefault(dest, []).append(dict(
            slot=slot, generation=self._slot_generation[slot], reason=reason,
            closed_at=now, n_boxes=len(items), items=items))
        self._completed.setdefault(slot, []).append(pallet)

    def finalize_pallets(self, now):
        for dest, slots in self._choices.items():
            remaining = [slot for slot in slots if self._pallets[slot].placements]
            # Orden determinista de último uso: solo el último palet queda END.
            remaining.sort(key=lambda slot: (self._last_commit.get(slot, 0), slot))
            for index, slot in enumerate(remaining):
                self._close_slot(slot, "END" if index == len(remaining) - 1 else "DRAIN", now)
        counts = {dest: [r["n_boxes"] for r in rows] for dest, rows in self._records.items()}
        reasons = {dest: [r["reason"] for r in rows] for dest, rows in self._records.items()}
        return counts, reasons

    def export_committed_placements(self):
        return {str(dest): [dict(item) for rec in rows for item in rec["items"]]
                for dest, rows in self._records.items()}

    def collect_kpis(self):
        result = super().collect_kpis()
        groups = {}
        for slot, pallets in self._completed.items():
            groups.setdefault(self._slot_dest[slot], []).extend(pallets)
        result.update(aggregate_pallet_kpis(groups))
        result["closures_by_reason"] = dict(Counter(r["reason"] for rows in self._records.values() for r in rows))
        result.update(open_pallets=self.open_pallets,
                      open_pallets_lever="operational_extra_pallet_positions",
                      open_pallets_closure_rule="NO_VISIBLE_FIT; terminal DRAIN except last END",
                      physical_pallets={dest: [{k: v for k, v in r.items() if k != "items"} for r in rows]
                                        for dest, rows in self._records.items()},
                      slot_changeovers=self._slot_closures,
                      slot_changeover_time_s=self._slot_closures * self.t_changeover)
        return result
