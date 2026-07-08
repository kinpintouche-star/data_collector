from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


SUPPORTED_TIMEFRAMES = {"H4", "H1", "M30", "M15", "M5", "M1"}


BLOCK_CATALOG: dict[str, dict[str, Any]] = {
    "trigger.crt": {
        "label": "CRT",
        "category": "trigger",
        "timeframes": ["H4", "H1", "M15", "M1"],
        "outputs": ["crt"],
        "params": {
            "model": ["sweep_back_in", "body_inside"],
            "detect_c3": True,
        },
    },
    "trigger.swing_sequence": {
        "label": "Swing sequence",
        "category": "trigger",
        "timeframes": ["H1", "M15", "M5", "M1"],
        "outputs": ["s1", "s2"],
        "params": {"patterns": ["swing_swing", "swing_inner"]},
    },
    "trigger.leg": {
        "label": "Impulse leg",
        "category": "trigger",
        "timeframes": ["M15", "M5", "M1"],
        "outputs": ["leg"],
        "params": {"anchor": "s2_to_next_opposite_pivot"},
    },
    "trigger.immediate_rebalance": {
        "label": "Immediate Rebalance",
        "category": "trigger",
        "timeframes": ["M15", "M5", "M1"],
        "outputs": ["ir"],
        "params": {
            "tolerance_ticks": 1,
            "min_impulse_body_ratio": 0.55,
            "min_impulse_body_ticks": 4,
            "require_rejection_close": True,
            "extension_candles": 2,
        },
    },
    "trigger.bos_mss": {
        "label": "BOS / MSS",
        "category": "trigger",
        "timeframes": ["H1", "M15", "M5", "M1"],
        "outputs": ["structure_break"],
        "experimental": True,
        "params": {
            "mode": ["BOS", "MSS", "BOS_OR_MSS"],
            "direction": ["follow_signal", "bullish", "bearish"],
            "lookback_bars": 48,
        },
    },
    "detect.session_range": {
        "label": "Session range",
        "category": "detect",
        "timeframes": ["M1"],
        "outputs": ["session_range"],
        "params": {
            "sessions": ["asian", "london", "new_york"],
            "timezone": "America/New_York",
        },
    },
    "detect.amd_phase": {
        "label": "AMD phase",
        "category": "detect",
        "timeframes": ["H1", "M15", "M5", "M1"],
        "outputs": ["amd_phase"],
        "experimental": True,
        "params": {
            "phases": ["accumulation_candidate", "distribution_candidate"],
            "range_bars": 30,
            "lookback_bars": 96,
        },
    },
    "compute.target": {
        "label": "Target",
        "category": "compute",
        "timeframes": ["D1", "H4", "H1", "M15", "M1"],
        "outputs": ["target"],
        "params": {
            "mode": "crt_then_nearest_liquidity",
            "levels": [
                "previous_day_high_low",
                "previous_week_high_low",
                "previous_month_high_low",
                "asian_high_low",
                "london_high_low",
                "new_york_high_low",
                "equal_highs_lows",
                "h1_m15_swings",
            ],
        },
    },
    "compute.fibonacci": {
        "label": "Fibonacci",
        "category": "compute",
        "timeframes": ["M15", "M5", "M1"],
        "outputs": ["fib", "ote"],
        "params": {"start_ref": "leg.start", "end_ref": "leg.end", "deep": 0.79},
    },
    "condition.retracement": {
        "label": "Retracement",
        "category": "condition",
        "timeframes": ["M15", "M5", "M1"],
        "outputs": ["pd_array"],
        "params": {"zone_types": ["OB", "FVG"], "zone": "OTE", "confirmation": "rejection"},
    },
    "filter.bias": {
        "label": "HTF bias",
        "category": "filter",
        "timeframes": ["H4", "H1", "M15"],
        "outputs": ["bias"],
        "params": {"timeframes": ["H1", "M15"], "method": "swing_structure", "neutral_policy": "reject"},
    },
    "filter.trend": {
        "label": "Trend filter",
        "category": "filter",
        "timeframes": ["H4", "H1", "M30", "M15", "M5", "M1"],
        "outputs": ["trend"],
        "params": {
            "method": ["swing_structure"],
            "direction": ["follow_signal", "follow_parent", "bullish", "bearish"],
            "lookback": ["all_known", "rolling_day", "rolling_week", "rolling_month"],
            "neutral_policy": ["reject", "allow"],
        },
    },
    "filter.confluence": {
        "label": "HTF confluence",
        "category": "filter",
        "timeframes": ["H4", "H1", "M15"],
        "outputs": ["confluence"],
        "params": {"timeframes": ["H1", "M15"], "zone_types": ["OB", "FVG"], "overlap_tolerance_ticks": 4},
    },
    "action.order": {
        "label": "Order",
        "category": "action",
        "timeframes": ["M1"],
        "outputs": ["order"],
        "params": {
            "entry": "signal_close",
            "take_profit": "crt_or_nearest_liquidity",
            "stop_loss": "structural_pd_array",
            "min_rr": 2.0,
            "max_rr_multiplier_from_initial": 2.0,
        },
    },
}


DEFAULT_OUTPUTS = {block_type: tuple(item["outputs"]) for block_type, item in BLOCK_CATALOG.items()}


class StrategyBlock(BaseModel):
    id: str
    type: str
    label: str | None = None
    timeframe: str | None = None
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)

    @field_validator("timeframe")
    @classmethod
    def uppercase_timeframe(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class StrategyDefinitionPayload(BaseModel):
    global_params: dict[str, Any] = Field(default_factory=dict)
    timeframes: list[str] = Field(default_factory=lambda: ["H4", "M15", "M1"])
    blocks: list[StrategyBlock] = Field(min_length=1)
    execution: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timeframes")
    @classmethod
    def uppercase_timeframes(cls, value: list[str]) -> list[str]:
        return [item.upper() for item in value]

    @model_validator(mode="after")
    def validate_definition(self) -> "StrategyDefinitionPayload":
        errors = validate_strategy_definition(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class StrategyDefinitionCreate(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str | None = None
    definition: StrategyDefinitionPayload | None = None
    template_id: str | None = None


class StrategyDefinitionUpdate(BaseModel):
    name: str | None = None
    version: str | None = None
    description: str | None = None
    definition: StrategyDefinitionPayload | None = None


class StrategyDefinitionPublic(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    status: Literal["draft", "validated", "archived"]
    description: str | None = None
    definition: dict[str, Any]
    definition_hash: str
    exported_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StrategyValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    definition_hash: str | None = None


def catalog_payload() -> dict[str, Any]:
    return {
        "blocks": BLOCK_CATALOG,
        "indicators": [
            {"id": "order_block", "label": "Order Block", "implemented_as": "condition.retracement zone_types=OB"},
            {"id": "fair_value_gap", "label": "Fair Value Gap", "implemented_as": "condition.retracement zone_types=FVG"},
            {"id": "immediate_rebalance", "label": "Immediate Rebalance", "implemented_as": "trigger.immediate_rebalance"},
            {"id": "bos_mss", "label": "BOS / MSS", "implemented_as": "trigger.bos_mss", "experimental": True},
            {"id": "liquidity_targets", "label": "PD/PW/PM/Session/Equal Targets", "implemented_as": "compute.target"},
            {"id": "trend", "label": "Multi-timeframe Trend", "implemented_as": "filter.trend"},
            {"id": "amd_phase", "label": "AMD Phase", "implemented_as": "detect.amd_phase", "experimental": True},
        ],
        "timeframes": sorted(SUPPORTED_TIMEFRAMES),
        "templates": [
            {"id": item["id"], "name": item["name"], "description": item["description"]}
            for item in strategy_templates()
        ],
    }


def strategy_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "crt_h1_m1",
            "name": "CRT H1 M1",
            "description": "Reconstruction de la strategie actuelle: CRT H1, structure M15, execution M1 OB/FVG.",
            "definition": _current_clone_definition(),
        },
        {
            "id": "ict_crt_m1_liquidity_confluence_v0",
            "name": "ICT CRT M1 Liquidity Confluence V0",
            "description": "Pipeline ICT/SMC v1 avec CRT, swings, fibo, retracement OB/FVG et RR minimum.",
            "definition": _liquidity_confluence_definition(),
        },
        {
            "id": "immediate_rebalance_h1_m15_m1",
            "name": "Immediate Rebalance H1 M15 M1",
            "description": "CRT H1, swing de protection M15, puis entree M1 sur Immediate Rebalance.",
            "definition": _immediate_rebalance_definition(),
        },
        {
            "id": "ir_liquidity_targets_experimental",
            "name": "IR + Liquidity Targets",
            "description": "Experimental: Immediate Rebalance avec PD/PW/PM, sessions et equal highs/lows comme cibles.",
            "definition": _ir_liquidity_targets_definition(),
        },
        {
            "id": "trend_aligned_crt_experimental",
            "name": "Trend Aligned CRT",
            "description": "Experimental: CRT H1/M1 filtre par tendance H1 puis M15 avant execution.",
            "definition": _trend_aligned_crt_definition(),
        },
        {
            "id": "amd_range_sweep_experimental",
            "name": "AMD Range Sweep",
            "description": "Experimental: detection range/sweep/displacement avant execution ICT.",
            "definition": _amd_range_sweep_definition(),
        },
    ]


def template_by_id(template_id: str) -> dict[str, Any]:
    for template in strategy_templates():
        if template["id"] == template_id:
            return template
    raise ValueError(f"Unknown strategy template: {template_id}")


def validate_strategy_definition(definition: StrategyDefinitionPayload | dict[str, Any]) -> list[str]:
    payload = _unchecked_payload(definition)
    errors: list[str] = []
    seen_block_ids: set[str] = set()
    known_outputs: set[str] = set()

    for timeframe in payload.timeframes:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            errors.append(f"Unsupported timeframe: {timeframe}")

    for index, block in enumerate(payload.blocks, start=1):
        if not block.id:
            errors.append(f"Block #{index} has no id.")
            continue
        if block.id in seen_block_ids:
            errors.append(f"Duplicate block id: {block.id}")
        seen_block_ids.add(block.id)

        catalog = BLOCK_CATALOG.get(block.type)
        if catalog is None:
            errors.append(f"Block {block.id}: unknown type {block.type}")
            continue
        if block.timeframe and block.timeframe not in set(catalog["timeframes"]) | SUPPORTED_TIMEFRAMES:
            errors.append(f"Block {block.id}: unsupported timeframe {block.timeframe}")
        if block.timeframe and block.timeframe not in SUPPORTED_TIMEFRAMES and block.timeframe != "D1":
            errors.append(f"Block {block.id}: timeframe {block.timeframe} is not supported by the engine")

        errors.extend(_required_param_errors(block))
        errors.extend(_reference_errors(block, known_outputs))

        outputs = block.outputs or list(DEFAULT_OUTPUTS.get(block.type, ()))
        for output in outputs:
            known_outputs.add(output)
            known_outputs.add(f"{block.id}.{output}")
        known_outputs.add(block.id)

    if not any(block.enabled and block.type == "action.order" for block in payload.blocks):
        errors.append("A strategy needs one enabled action.order block.")
    if not any(block.enabled and block.type == "trigger.crt" for block in payload.blocks):
        errors.append("A strategy needs one enabled trigger.crt block.")

    return errors


def validation_result(definition: StrategyDefinitionPayload | dict[str, Any]) -> StrategyValidationResult:
    try:
        payload = _unchecked_payload(definition)
    except Exception as exc:  # noqa: BLE001 - surfaced to local strategy builder UI
        return StrategyValidationResult(valid=False, errors=[str(exc)])
    errors = validate_strategy_definition(payload)
    warnings = _definition_warnings(payload)
    return StrategyValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        definition_hash=definition_hash(payload.model_dump(mode="json")),
    )


def _unchecked_payload(definition: StrategyDefinitionPayload | dict[str, Any]) -> StrategyDefinitionPayload:
    if isinstance(definition, StrategyDefinitionPayload):
        return definition
    blocks = [StrategyBlock.model_validate(block) for block in definition.get("blocks", [])]
    return StrategyDefinitionPayload.model_construct(
        global_params=definition.get("global_params", {}),
        timeframes=[str(item).upper() for item in definition.get("timeframes", ["M1"])],
        blocks=blocks,
        execution=definition.get("execution", {}),
    )


def definition_hash(definition: dict[str, Any]) -> str:
    payload = json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_strategy_yaml(name: str, version: str, definition: dict[str, Any], directory: Path | str = "configs") -> str:
    slug = _slugify(name)
    version_slug = _slugify(version)
    path = Path(directory) / f"strategy_builder_{slug}_{version_slug}.yaml"
    export_payload = {
        "strategy_builder": True,
        "name": name,
        "version": version,
        "definition": definition,
    }
    path.write_text(yaml.safe_dump(export_payload, sort_keys=False), encoding="utf-8")
    return str(path.as_posix())


def builder_yaml_payload(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if payload.get("strategy_builder") is not True or "definition" not in payload:
        raise ValueError(f"Not a strategy builder YAML: {path}")
    return payload


def _required_param_errors(block: StrategyBlock) -> list[str]:
    params = block.params or {}
    errors: list[str] = []
    if block.type == "condition.retracement" and not params.get("zone_types"):
        errors.append(f"Block {block.id}: condition.retracement requires zone_types.")
    if block.type == "filter.bias" and not params.get("timeframes"):
        errors.append(f"Block {block.id}: filter.bias requires timeframes.")
    if block.type == "filter.trend" and not params.get("method"):
        errors.append(f"Block {block.id}: filter.trend requires method.")
    if block.type == "trigger.bos_mss":
        if not params.get("mode"):
            errors.append(f"Block {block.id}: trigger.bos_mss requires mode.")
        if not params.get("direction"):
            errors.append(f"Block {block.id}: trigger.bos_mss requires direction.")
    if block.type == "detect.session_range":
        if not params.get("sessions"):
            errors.append(f"Block {block.id}: detect.session_range requires sessions.")
        if not params.get("timezone"):
            errors.append(f"Block {block.id}: detect.session_range requires timezone.")
    if block.type == "detect.amd_phase":
        if not params.get("phases"):
            errors.append(f"Block {block.id}: detect.amd_phase requires phases.")
        if params.get("range_bars") is None:
            errors.append(f"Block {block.id}: detect.amd_phase requires range_bars.")
    if block.type == "action.order":
        if params.get("min_rr") is None:
            errors.append(f"Block {block.id}: action.order requires min_rr.")
        if not params.get("take_profit"):
            errors.append(f"Block {block.id}: action.order requires take_profit.")
        if not params.get("stop_loss"):
            errors.append(f"Block {block.id}: action.order requires stop_loss.")
    return errors


def _reference_errors(block: StrategyBlock, known_outputs: set[str]) -> list[str]:
    errors: list[str] = []
    for key, value in _walk_params(block.params):
        if not key.endswith("_ref"):
            continue
        if isinstance(value, str) and value not in known_outputs:
            errors.append(f"Block {block.id}: reference {value} is not produced by previous blocks.")
    return errors


def _walk_params(params: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(params, dict):
        for key, value in params.items():
            next_key = f"{prefix}.{key}" if prefix else key
            found.append((key, value))
            found.extend(_walk_params(value, next_key))
    elif isinstance(params, list):
        for item in params:
            found.extend(_walk_params(item, prefix))
    return found


def _definition_warnings(payload: StrategyDefinitionPayload) -> list[str]:
    warnings = []
    if any(block.enabled and block.type == "filter.confluence" for block in payload.blocks):
        warnings.append("Confluence v1 is enforced as a coarse OB/FVG overlap filter.")
    if not any(block.enabled and block.type in {"filter.bias", "filter.trend"} for block in payload.blocks):
        warnings.append("No HTF bias or trend filter configured.")
    return warnings


def _current_clone_definition() -> dict[str, Any]:
    return {
        "global_params": {"timezone": "America/New_York", "trade_direction": "auto"},
        "timeframes": ["H1", "M15", "M1"],
        "blocks": [
            _block("h1_crt", "trigger.crt", "H1", {"model": "sweep_back_in", "detect_c3": True}),
            _block("h1_trend", "filter.trend", "H1", {"method": "swing_structure", "direction": "follow_signal", "lookback": "rolling_day", "neutral_policy": "allow"}),
            _block("m15_trend", "filter.trend", "M15", {"method": "swing_structure", "direction": "follow_parent", "lookback": "rolling_day", "neutral_policy": "allow"}),
            _block("m15_swings", "trigger.swing_sequence", "M15", {"patterns": ["swing_swing", "swing_inner"]}),
            _block("m15_leg", "trigger.leg", "M15", {"anchor": "s2_to_next_opposite_pivot"}),
            _block("m1_fib", "compute.fibonacci", "M1", {"start_ref": "m15_swings.s2", "end_ref": "m15_leg.leg", "deep": 0.79}),
            _block("m1_retracement", "condition.retracement", "M1", {"zone_types": ["OB", "FVG"], "zone": "OTE", "confirmation": "rejection"}),
            _block("m1_order", "action.order", "M1", {"take_profit": "crt_objective", "stop_loss": "structural_pd_array", "min_rr": 0.1, "fill_policy": "signal_close"}),
        ],
        "execution": {"initial_balance": 100000, "order_qty": 1.0, "fill_policy": "signal_close"},
    }


def _liquidity_confluence_definition() -> dict[str, Any]:
    return {
        "global_params": {"timezone": "America/New_York", "trade_direction": "auto"},
        "timeframes": ["H4", "H1", "M15", "M1"],
        "blocks": [
            _block("h4_crt", "trigger.crt", "H4", {"model": "sweep_back_in", "detect_c3": True}),
            _block("h1_m15_bias", "filter.bias", "H1", {"timeframes": ["H1", "M15"], "method": "swing_structure", "neutral_policy": "reject"}),
            _block("m15_swings", "trigger.swing_sequence", "M15", {"patterns": ["swing_swing", "swing_inner"]}),
            _block("m1_leg", "trigger.leg", "M1", {"anchor": "s2_to_next_opposite_pivot"}),
            _block(
                "m1_target",
                "compute.target",
                "M1",
                {
                    "mode": "crt_then_nearest_liquidity",
                    "levels": [
                        "previous_day_high_low",
                        "previous_week_high_low",
                        "previous_month_high_low",
                        "asian_high_low",
                        "london_high_low",
                        "new_york_high_low",
                        "equal_highs_lows",
                        "h1_m15_swings",
                    ],
                },
            ),
            _block("m1_fib", "compute.fibonacci", "M1", {"start_ref": "m15_swings.s2", "end_ref": "m1_leg.leg", "target_ref": "m1_target.target", "deep": 0.79}),
            _block("m1_retracement", "condition.retracement", "M1", {"zone_types": ["OB", "FVG"], "zone": "OTE", "confirmation": "rejection"}),
            _block("m1_order", "action.order", "M1", {"take_profit": "crt_or_nearest_liquidity", "stop_loss": "structural_pd_array", "min_rr": 2.0, "max_rr_multiplier_from_initial": 2.0, "fill_policy": "signal_close"}),
        ],
        "execution": {"initial_balance": 100000, "order_qty": 1.0, "fill_policy": "signal_close"},
    }


def _immediate_rebalance_definition() -> dict[str, Any]:
    return {
        "global_params": {"timezone": "America/New_York", "trade_direction": "auto"},
        "timeframes": ["H1", "M15", "M1"],
        "blocks": [
            _block("h1_crt", "trigger.crt", "H1", {"model": "sweep_back_in", "detect_c3": True}),
            _block("h1_trend", "filter.trend", "H1", {"method": "swing_structure", "direction": "follow_signal", "lookback": "rolling_day", "neutral_policy": "allow"}),
            _block("m15_trend", "filter.trend", "M15", {"method": "swing_structure", "direction": "follow_parent", "lookback": "rolling_day", "neutral_policy": "allow"}),
            _block("m15_swings", "trigger.swing_sequence", "M15", {"patterns": ["swing_swing", "swing_inner"]}),
            _block(
                "m1_immediate_rebalance",
                "trigger.immediate_rebalance",
                "M1",
                {
                    "tolerance_ticks": 1,
                    "min_impulse_body_ratio": 0.55,
                    "min_impulse_body_ticks": 4,
                    "require_rejection_close": True,
                    "extension_candles": 2,
                },
            ),
            _block(
                "m1_target",
                "compute.target",
                "M1",
                {
                    "mode": "crt_then_nearest_liquidity",
                    "levels": [
                        "previous_day_high_low",
                        "previous_week_high_low",
                        "previous_month_high_low",
                        "asian_high_low",
                        "london_high_low",
                        "new_york_high_low",
                        "equal_highs_lows",
                        "h1_m15_swings",
                    ],
                },
            ),
            _block(
                "m1_order",
                "action.order",
                "M1",
                {
                    "take_profit": "crt_or_nearest_liquidity",
                    "stop_loss": "immediate_rebalance_origin",
                    "min_rr": 2.0,
                    "max_rr_multiplier_from_initial": 2.0,
                    "buffer_ticks": 1,
                    "fill_policy": "signal_close",
                },
            ),
        ],
        "execution": {"initial_balance": 100000, "order_qty": 1.0, "fill_policy": "signal_close"},
    }


def _ir_liquidity_targets_definition() -> dict[str, Any]:
    definition = json.loads(json.dumps(_immediate_rebalance_definition()))
    definition["blocks"].insert(
        5,
        _block(
            "m1_sessions",
            "detect.session_range",
            "M1",
            {"sessions": ["asian", "london", "new_york"], "timezone": "America/New_York"},
        ),
    )
    definition["blocks"].insert(
        6,
        _block(
            "m1_bos_mss",
            "trigger.bos_mss",
            "M1",
            {"mode": "BOS_OR_MSS", "direction": "follow_signal", "lookback_bars": 48},
        ),
    )
    return definition


def _trend_aligned_crt_definition() -> dict[str, Any]:
    return {
        "global_params": {"timezone": "America/New_York", "trade_direction": "auto"},
        "timeframes": ["H1", "M15", "M1"],
        "blocks": [
            _block("h1_crt", "trigger.crt", "H1", {"model": "sweep_back_in", "detect_c3": True}),
            _block("h1_trend", "filter.trend", "H1", {"method": "swing_structure", "direction": "follow_signal", "lookback": "rolling_day", "neutral_policy": "reject"}),
            _block("m15_trend", "filter.trend", "M15", {"method": "swing_structure", "direction": "follow_parent", "lookback": "rolling_day", "neutral_policy": "reject"}),
            _block("m15_swings", "trigger.swing_sequence", "M15", {"patterns": ["swing_swing", "swing_inner"]}),
            _block("m1_leg", "trigger.leg", "M1", {"anchor": "s2_to_next_opposite_pivot"}),
            _block(
                "m1_target",
                "compute.target",
                "M1",
                {
                    "mode": "crt_then_nearest_liquidity",
                    "levels": [
                        "previous_day_high_low",
                        "previous_week_high_low",
                        "previous_month_high_low",
                        "asian_high_low",
                        "london_high_low",
                        "new_york_high_low",
                        "equal_highs_lows",
                        "h1_m15_swings",
                    ],
                },
            ),
            _block("m1_fib", "compute.fibonacci", "M1", {"start_ref": "m15_swings.s2", "end_ref": "m1_leg.leg", "target_ref": "m1_target.target", "deep": 0.79}),
            _block("m1_retracement", "condition.retracement", "M1", {"zone_types": ["OB", "FVG"], "zone": "OTE", "confirmation": "rejection"}),
            _block("m1_order", "action.order", "M1", {"take_profit": "crt_or_nearest_liquidity", "stop_loss": "structural_pd_array", "min_rr": 2.0, "max_rr_multiplier_from_initial": 2.0, "fill_policy": "signal_close"}),
        ],
        "execution": {"initial_balance": 100000, "order_qty": 1.0, "fill_policy": "signal_close"},
    }


def _amd_range_sweep_definition() -> dict[str, Any]:
    definition = json.loads(json.dumps(_trend_aligned_crt_definition()))
    definition["blocks"].insert(
        3,
        _block(
            "m15_amd",
            "detect.amd_phase",
            "M15",
            {"phases": ["accumulation_candidate", "distribution_candidate"], "range_bars": 24, "lookback_bars": 48},
        ),
    )
    return definition


def _block(block_id: str, block_type: str, timeframe: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": block_id,
        "type": block_type,
        "timeframe": timeframe,
        "enabled": True,
        "params": params,
        "outputs": list(DEFAULT_OUTPUTS.get(block_type, ())),
    }


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "strategy"
