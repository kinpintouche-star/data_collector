import { useState } from "react";
import {
  Archive,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  Eye,
  EyeOff,
  Plus,
  Save,
  TestTube2,
  Trash2,
  X
} from "lucide-react";
import type {
  StrategyBlock,
  StrategyBuilderCatalog,
  StrategyDefinition,
  StrategyDefinitionPayload,
  StrategyValidationResult
} from "../types";

type StrategyBuilderProps = {
  catalog: StrategyBuilderCatalog | null;
  strategies: StrategyDefinition[];
  selectedId: string;
  selected: StrategyDefinition | null;
  selectedBlockId: string;
  loading: boolean;
  error: string | null;
  validation: StrategyValidationResult | null;
  onSelect: (strategyId: string) => void;
  onSelectBlock: (blockId: string) => void;
  onCreateFromTemplate: (templateId: string) => void;
  onDuplicate: (strategy: StrategyDefinition) => void;
  onDelete: (strategyId: string) => void;
  onSave: (strategyId: string, payload: StrategyDefinitionPayload) => void;
  onValidate: (strategyId: string) => void;
  onExport: (strategyId: string) => void;
  onUseInRunLab: (strategy: StrategyDefinition) => void;
};

function cloneDefinition(definition: StrategyDefinitionPayload): StrategyDefinitionPayload {
  return JSON.parse(JSON.stringify(definition)) as StrategyDefinitionPayload;
}

function blockTitle(block: StrategyBlock, catalog: StrategyBuilderCatalog | null): string {
  return block.label || catalog?.blocks[block.type]?.label || block.type;
}

function defaultBlock(blockType: string, catalog: StrategyBuilderCatalog): StrategyBlock {
  const item = catalog.blocks[blockType];
  const tf = item.timeframes.includes("M1") ? "M1" : item.timeframes[0] ?? "M1";
  return {
    id: `${blockType.replace(/[^a-z0-9]+/gi, "_")}_${Date.now().toString(36)}`,
    type: blockType,
    timeframe: tf === "D1" ? "M1" : tf,
    enabled: true,
    params: item.params ?? {},
    outputs: item.outputs ?? []
  };
}

function updateBlock(definition: StrategyDefinitionPayload, blockId: string, nextBlock: StrategyBlock): StrategyDefinitionPayload {
  const next = cloneDefinition(definition);
  next.blocks = next.blocks.map((block) => (block.id === blockId ? nextBlock : block));
  return next;
}

function moveBlock(definition: StrategyDefinitionPayload, blockId: string, delta: number): StrategyDefinitionPayload {
  const next = cloneDefinition(definition);
  const index = next.blocks.findIndex((block) => block.id === blockId);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= next.blocks.length) {
    return next;
  }
  const [block] = next.blocks.splice(index, 1);
  next.blocks.splice(target, 0, block);
  return next;
}

const multiChoiceParams = new Set(["levels", "patterns", "timeframes", "zone_types", "sessions", "phases"]);

const paramOptions: Record<string, Record<string, string[]>> = {
  "compute.target": {
    mode: ["crt_then_nearest_liquidity", "nearest_liquidity", "crt_objective"]
  },
  "condition.retracement": {
    zone: ["OTE", "0.5", "ANY"],
    confirmation: ["rejection", "touch"]
  },
  "filter.bias": {
    method: ["swing_structure"],
    neutral_policy: ["reject", "allow"]
  },
  "filter.trend": {
    method: ["swing_structure"],
    direction: ["follow_signal", "follow_parent", "bullish", "bearish"],
    lookback: ["all_known", "rolling_day", "rolling_week", "rolling_month"],
    neutral_policy: ["reject", "allow"]
  },
  "trigger.bos_mss": {
    mode: ["BOS", "MSS", "BOS_OR_MSS"],
    direction: ["follow_signal", "bullish", "bearish"]
  },
  "detect.session_range": {
    sessions: ["asian", "london", "new_york"]
  },
  "detect.amd_phase": {
    phases: ["accumulation_candidate", "distribution_candidate"]
  },
  "action.order": {
    entry: ["signal_close", "next_open"],
    fill_policy: ["signal_close", "next_open"],
    take_profit: ["crt_objective", "crt_or_nearest_liquidity", "nearest_liquidity"],
    stop_loss: ["structural_pd_array", "immediate_rebalance_origin"]
  },
  "trigger.leg": {
    anchor: ["s2_to_next_opposite_pivot"]
  }
};

const paramLabels: Record<string, string> = {
  anchor: "Ancrage",
  buffer_ticks: "Buffer SL (ticks)",
  confirmation: "Confirmation",
  deep: "Fibo profond",
  detect_c3: "Detecter C3",
  end_ref: "Fin",
  entry: "Entree",
  extension_candles: "Bougies extension",
  fill_policy: "Execution",
  levels: "Niveaux cibles",
  lookback: "Fenetre",
  lookback_bars: "Fenetre (barres)",
  max_rr_multiplier_from_initial: "Extension RR max",
  method: "Methode",
  min_impulse_body_ratio: "Corps displacement min",
  min_impulse_body_ticks: "Displacement min (ticks)",
  min_rr: "RR minimum",
  mode: "Mode",
  model: "Mode CRT",
  neutral_policy: "Neutre",
  patterns: "Patterns",
  phases: "Phases",
  range_bars: "Range (barres)",
  require_rejection_close: "Rejet obligatoire",
  sessions: "Sessions",
  start_ref: "Debut",
  stop_loss: "Stop loss",
  take_profit: "Take profit",
  target_ref: "Target",
  timeframe: "Timeframe",
  timeframes: "Timeframes",
  timezone: "Timezone",
  tolerance_ticks: "Tolerance (ticks)",
  zone: "Zone",
  zone_types: "Types de zone"
};

function labelForParam(key: string): string {
  return paramLabels[key] ?? key.replace(/_/g, " ");
}

function optionsForParam(block: StrategyBlock, key: string, catalog: StrategyBuilderCatalog | null): string[] | null {
  const configured = paramOptions[block.type]?.[key];
  if (configured) {
    return configured;
  }
  const catalogValue = catalog?.blocks[block.type]?.params?.[key];
  if (Array.isArray(catalogValue)) {
    return catalogValue.map(String);
  }
  return null;
}

function paramKeys(block: StrategyBlock, catalog: StrategyBuilderCatalog | null): string[] {
  return Array.from(
    new Set([
      ...Object.keys(catalog?.blocks[block.type]?.params ?? {}),
      ...Object.keys(block.params ?? {})
    ])
  );
}

export function StrategyBuilder({
  catalog,
  strategies,
  selectedId,
  selected,
  selectedBlockId,
  loading,
  error,
  validation,
  onSelect,
  onSelectBlock,
  onCreateFromTemplate,
  onDuplicate,
  onDelete,
  onSave,
  onValidate,
  onExport,
  onUseInRunLab
}: StrategyBuilderProps) {
  const [deleteArmed, setDeleteArmed] = useState(false);
  const selectedBlock = selected?.definition.blocks.find((block) => block.id === selectedBlockId) ?? selected?.definition.blocks[0] ?? null;

  const saveDefinition = (definition: StrategyDefinitionPayload) => {
    if (selected) {
      onSave(selected.id, definition);
    }
  };

  const addBlock = (blockType: string) => {
    if (!selected || !catalog) {
      return;
    }
    const next = cloneDefinition(selected.definition);
    const block = defaultBlock(blockType, catalog);
    next.blocks.push(block);
    onSelectBlock(block.id);
    saveDefinition(next);
  };

  const patchBlock = (patch: Partial<StrategyBlock>) => {
    if (!selected || !selectedBlock) {
      return;
    }
    saveDefinition(updateBlock(selected.definition, selectedBlock.id, { ...selectedBlock, ...patch }));
  };

  const patchParam = (key: string, value: unknown) => {
    if (!selectedBlock) {
      return;
    }
    patchBlock({ params: { ...(selectedBlock.params ?? {}), [key]: value } });
  };

  const strategyLocked = selected?.status !== "draft";

  return (
    <section className="strategy-builder">
      <header className="builder-header">
        <div>
          <span className="eyebrow">Strategy Builder</span>
          <h1>Créer une stratégie</h1>
        </div>
        <div className="toolbar">
          <button className="icon-button" disabled={!selected || loading} onClick={() => selected && onDuplicate(selected)} type="button">
            <Copy size={17} />
            <span>Dupliquer</span>
          </button>
          <button
            className="icon-button danger"
            disabled={!selected || loading}
            onClick={() => setDeleteArmed(true)}
            type="button"
          >
            <Trash2 size={17} />
            <span>Supprimer</span>
          </button>
          <button className="icon-button" disabled={!selected || loading} onClick={() => selected && onValidate(selected.id)} type="button">
            <Check size={17} />
            <span>Valider</span>
          </button>
          <button className="icon-button" disabled={!selected || loading} onClick={() => selected && onExport(selected.id)} type="button">
            <Download size={17} />
            <span>Export</span>
          </button>
          <button className="primary-inline" disabled={!selected || loading} onClick={() => selected && onUseInRunLab(selected)} type="button">
            <TestTube2 size={17} />
            <span>Tester</span>
          </button>
        </div>
      </header>

      {error && <div className="alert error">{error}</div>}
      {deleteArmed && selected && (
        <div className="alert warning inline-confirm">
          <span>Supprimer {selected.name} ?</span>
          <button className="icon-button" onClick={() => setDeleteArmed(false)} type="button">
            <X size={16} />
            <span>Annuler</span>
          </button>
          <button
            className="icon-button danger"
            disabled={loading}
            onClick={() => {
              onDelete(selected.id);
              setDeleteArmed(false);
            }}
            type="button"
          >
            <Trash2 size={16} />
            <span>Confirmer</span>
          </button>
        </div>
      )}
      {validation && (
        <div className={validation.valid ? "alert success" : "alert error"}>
          {validation.valid ? "Definition validee." : validation.errors.join(" ")}
          {validation.warnings.length ? ` ${validation.warnings.join(" ")}` : ""}
        </div>
      )}

      <div className="builder-layout">
        <aside className="builder-sidebar">
          <section>
            <h2>Templates</h2>
            <div className="template-list">
              {(catalog?.templates ?? []).map((template) => (
                <button disabled={loading} key={template.id} onClick={() => onCreateFromTemplate(template.id)} type="button">
                  <Plus size={16} />
                  <span>{template.name}</span>
                </button>
              ))}
            </div>
          </section>

          <section>
            <h2>Stratégies</h2>
            <div className="strategy-list">
              {strategies.map((strategy) => (
                <button
                  className={strategy.id === selectedId ? "strategy-list-item is-selected" : "strategy-list-item"}
                  key={strategy.id}
                  onClick={() => onSelect(strategy.id)}
                  type="button"
                >
                  <strong>{strategy.name}</strong>
                  <small>
                    {strategy.version} | {strategy.status}
                  </small>
                </button>
              ))}
              {!strategies.length && <div className="empty-state">Aucune stratégie builder.</div>}
            </div>
          </section>
        </aside>

        <section className="builder-pipeline">
          <div className="pipeline-toolbar">
            <label className="field">
              <span>Ajouter</span>
              <select disabled={!selected || strategyLocked || !catalog} onChange={(event) => event.target.value && addBlock(event.target.value)} value="">
                <option value="">Bloc</option>
                {Object.entries(catalog?.blocks ?? {}).map(([type, item]) => (
                  <option key={type} value={type}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {selected && (
              <span className={`status-pill ${selected.status}`}>
                <Archive size={14} />
                {selected.status}
              </span>
            )}
          </div>

          <div className="block-stack">
            {(selected?.definition.blocks ?? []).map((block, index) => (
              <button
                className={block.id === selectedBlock?.id ? "builder-block is-selected" : "builder-block"}
                key={block.id}
                onClick={() => onSelectBlock(block.id)}
                type="button"
              >
                <span className="block-index">{index + 1}</span>
                <div>
                  <strong>
                    {blockTitle(block, catalog)}
                    {catalog?.blocks[block.type]?.experimental && <span className="mini-badge experimental">experimental</span>}
                  </strong>
                  <small>
                    {block.timeframe ?? "-"} | {block.type}
                  </small>
                </div>
                {block.enabled ? <Eye size={16} /> : <EyeOff size={16} />}
              </button>
            ))}
            {!selected && <div className="blank-slate">Sélectionne ou crée une stratégie.</div>}
          </div>
        </section>

        <aside className="builder-details">
          <h2>Paramètres</h2>
          {selectedBlock ? (
            <>
              <label className="field">
                <span>ID</span>
                <input disabled={strategyLocked} value={selectedBlock.id} onChange={(event) => patchBlock({ id: event.target.value })} />
              </label>
              <label className="field">
                <span>Type</span>
                <select disabled={strategyLocked} value={selectedBlock.type} onChange={(event) => patchBlock(defaultBlock(event.target.value, catalog!))}>
                  {Object.keys(catalog?.blocks ?? {}).map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Timeframe</span>
                <select disabled={strategyLocked} value={selectedBlock.timeframe ?? "M1"} onChange={(event) => patchBlock({ timeframe: event.target.value })}>
                  {(catalog?.timeframes ?? ["H4", "H1", "M15", "M1"]).map((timeframe) => (
                    <option key={timeframe} value={timeframe}>
                      {timeframe}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field checkbox-field">
                <input disabled={strategyLocked} checked={selectedBlock.enabled} onChange={(event) => patchBlock({ enabled: event.target.checked })} type="checkbox" />
                <span>Actif</span>
              </label>
              <div className="param-form">
                <h3>Parametres du bloc</h3>
                {paramKeys(selectedBlock, catalog).map((key) => {
                  const value = selectedBlock.params?.[key] ?? catalog?.blocks[selectedBlock.type]?.params?.[key];
                  const choices = optionsForParam(selectedBlock, key, catalog);
                  if (typeof value === "boolean") {
                    return (
                      <label className="field checkbox-field" key={key}>
                        <input disabled={strategyLocked} checked={value} onChange={(event) => patchParam(key, event.target.checked)} type="checkbox" />
                        <span>{labelForParam(key)}</span>
                      </label>
                    );
                  }
                  if (typeof value === "number") {
                    return (
                      <label className="field" key={key}>
                        <span>{labelForParam(key)}</span>
                        <input
                          disabled={strategyLocked}
                          min={0}
                          onChange={(event) => patchParam(key, Number(event.target.value))}
                          step={Number.isInteger(value) ? 1 : 0.01}
                          type="number"
                          value={Number(value)}
                        />
                      </label>
                    );
                  }
                  if (choices && multiChoiceParams.has(key)) {
                    const selectedValues = Array.isArray(value) ? value.map(String) : [];
                    return (
                      <fieldset className="param-choice-group" disabled={strategyLocked} key={key}>
                        <legend>{labelForParam(key)}</legend>
                        <div>
                          {choices.map((choice) => {
                            const checked = selectedValues.includes(choice);
                            return (
                              <label className={checked ? "choice-pill is-selected" : "choice-pill"} key={choice}>
                                <input
                                  checked={checked}
                                  onChange={(event) => {
                                    const next = event.target.checked
                                      ? [...selectedValues, choice]
                                      : selectedValues.filter((item) => item !== choice);
                                    patchParam(key, next);
                                  }}
                                  type="checkbox"
                                />
                                <span>{choice}</span>
                              </label>
                            );
                          })}
                        </div>
                      </fieldset>
                    );
                  }
                  if (choices) {
                    return (
                      <label className="field" key={key}>
                        <span>{labelForParam(key)}</span>
                        <select disabled={strategyLocked} onChange={(event) => patchParam(key, event.target.value)} value={String(value ?? choices[0] ?? "")}>
                          {choices.map((choice) => (
                            <option key={choice} value={choice}>
                              {choice}
                            </option>
                          ))}
                        </select>
                      </label>
                    );
                  }
                  return (
                    <label className="field" key={key}>
                      <span>{labelForParam(key)}</span>
                      <input disabled={strategyLocked} onChange={(event) => patchParam(key, event.target.value)} value={String(value ?? "")} />
                    </label>
                  );
                })}
              </div>
              <div className="detail-actions">
                <button disabled={strategyLocked} onClick={() => selected && saveDefinition(moveBlock(selected.definition, selectedBlock.id, -1))} type="button">
                  <ChevronUp size={16} />
                </button>
                <button disabled={strategyLocked} onClick={() => selected && saveDefinition(moveBlock(selected.definition, selectedBlock.id, 1))} type="button">
                  <ChevronDown size={16} />
                </button>
                <button
                  disabled={strategyLocked}
                  onClick={() => selected && saveDefinition(updateBlock(selected.definition, selectedBlock.id, { ...selectedBlock, enabled: !selectedBlock.enabled }))}
                  type="button"
                >
                  {selectedBlock.enabled ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
                <button disabled={strategyLocked} onClick={() => selected && saveDefinition(selected.definition)} type="button">
                  <Save size={16} />
                </button>
              </div>
            </>
          ) : (
            <div className="empty-state">Aucun bloc sélectionné.</div>
          )}
        </aside>
      </div>
    </section>
  );
}
