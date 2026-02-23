'use client';

import { useState, useEffect } from 'react';
import { useParams } from 'next/navigation';
import { API } from '../../lib/api';

// --- Types ---
interface TaskOutput {
  id: number;
  iteration: number;
  completed: string;
  capability_requested: string;
  agent_logs: string | null;
  output: string | null;
  error: string | null;
  llm_response_preview: string | null;
  model_used: string | null;
  image_used: string | null;
  duration_ms: number | null;
  deliverables: Record<string, string> | null;
  raw_result: any;
  created_at: string | null;
}

// --- Page ---
export default function TaskDetailPage() {
  const params = useParams();
  const taskId = params.id as string;

  const [timeline, setTimeline] = useState<any>(null);
  const [currentState, setCurrentState] = useState<any>(null);
  const [outputs, setOutputs] = useState<TaskOutput[]>([]);
  const [selectedDockerfile, setSelectedDockerfile] = useState<string | null>(null);
  const [expandedOutput, setExpandedOutput] = useState<number | null>(null);
  const [showRawJson, setShowRawJson] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'outputs' | 'timeline' | 'audit'>('outputs');
  const [showContinue, setShowContinue] = useState(false);
  const [followUp, setFollowUp] = useState('');
  const [continuing, setContinuing] = useState(false);

  // --- Data fetching ---
  useEffect(() => {
    const fetchData = async () => {
      try {
        const [timelineRes, stateRes, outputsRes] = await Promise.all([
          fetch(`${API}/api/tasks/${taskId}/execution-timeline`),
          fetch(`${API}/api/tasks/${taskId}/current-state`),
          fetch(`${API}/api/tasks/${taskId}/outputs`),
        ]);
        const timelineData = await timelineRes.json();
        const stateData = await stateRes.json();
        const outputsData = await outputsRes.json();

        setTimeline(timelineData);
        setCurrentState(stateData);
        setOutputs(outputsData.outputs || []);
      } catch (error) {
        console.error('Error fetching data:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [taskId]);

  // --- Continue task handler ---
  const handleContinue = async () => {
    if (!followUp.trim()) return;
    setContinuing(true);
    try {
      const res = await fetch(`${API}/api/tasks/${taskId}/continue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ follow_up: followUp }),
      });
      if (res.ok) {
        setShowContinue(false);
        setFollowUp('');
        // Data will refresh via the polling interval
      } else {
        const err = await res.json();
        alert(`Failed to continue: ${err.detail || 'Unknown error'}`);
      }
    } catch (e) {
      alert(`Failed to continue: ${e}`);
    } finally {
      setContinuing(false);
    }
  };

  // Auto-scroll chat
  // --- Helpers ---
  const getEventIcon = (event: string) => {
    switch (event) {
      case 'task_created': return '\u{1F4DD}';
      case 'task_started': return '\u25B6\uFE0F';
      case 'capability_requested': return '\u{1F510}';
      case 'capability_decided': return '\u2705';
      case 'task_completed': return '\u{1F3C1}';
      default: return '\u2022';
    }
  };

  const getEventColor = (event: string) => {
    switch (event) {
      case 'task_created': return 'border-blue-500';
      case 'task_started': return 'border-green-500';
      case 'capability_requested': return 'border-yellow-500';
      case 'capability_decided': return 'border-purple-500';
      case 'task_completed': return 'border-green-600';
      default: return 'border-gray-500';
    }
  };

  const extractLlmPreview = (o: TaskOutput): string | null => {
    if (o.llm_response_preview) return o.llm_response_preview;
    if (!o.output) return null;
    try {
      const parsed = JSON.parse(o.output);
      if (parsed.payloads) {
        const texts = parsed.payloads
          .map((p: any) => p.text)
          .filter(Boolean);
        if (texts.length) return texts.join('\n');
      }
    } catch (e) { /* ignore */ }
    return null;
  };

  const isBinaryContent = (content: string) => content.startsWith('base64:');

  const getMimeType = (filename: string): string => {
    const ext = filename.split('.').pop()?.toLowerCase() || '';
    const mimeMap: Record<string, string> = {
      pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
      gif: 'image/gif', svg: 'image/svg+xml', webp: 'image/webp', bmp: 'image/bmp',
      zip: 'application/zip', gz: 'application/gzip', csv: 'text/csv',
      mp3: 'audio/mpeg', mp4: 'video/mp4', wav: 'audio/wav',
    };
    return mimeMap[ext] || 'application/octet-stream';
  };

  const getFileIcon = (filename: string, binary: boolean): string => {
    if (!binary) return '\u{1F4C4}';
    const ext = filename.split('.').pop()?.toLowerCase() || '';
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(ext)) return '\u{1F5BC}';
    if (ext === 'pdf') return '\u{1F4D1}';
    if (['zip', 'tar', 'gz', '7z', 'rar'].includes(ext)) return '\u{1F4E6}';
    return '\u{1F4CE}';
  };

  const formatFileSize = (content: string, binary: boolean): string => {
    const bytes = binary ? Math.round((content.length - 7) * 0.75) : new Blob([content]).size;
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const downloadFile = (filename: string, content: string) => {
    let blob: Blob;
    if (isBinaryContent(content)) {
      const b64 = content.slice(7); // strip "base64:" prefix
      const binary = atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      blob = new Blob([bytes], { type: getMimeType(filename) });
    } else {
      blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const downloadAllDeliverables = (deliverables: Record<string, string>) => {
    Object.entries(deliverables).forEach(([filename, content], i) => {
      setTimeout(() => downloadFile(filename, content), i * 100);
    });
  };

  // --- Loading / Not found ---
  if (loading) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <p className="text-gray-500 text-sm">Loading task details...</p>
      </div>
    );
  }
  if (!timeline) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <p className="text-gray-500 text-sm">Task not found</p>
      </div>
    );
  }

  // --- Render ---
  return (
    <div className="p-8 max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-white mb-2">Task Execution Details</h1>
          <div className="flex items-center gap-3 text-sm text-gray-400 flex-wrap">
            <span className="font-mono">{timeline.task.id}</span>
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${
              timeline.task.status === 'completed' ? 'bg-green-900/60 text-green-300' :
              timeline.task.status === 'running'   ? 'bg-blue-900/60 text-blue-300' :
              timeline.task.status === 'failed'    ? 'bg-red-900/60 text-red-300' :
              'bg-gray-700 text-gray-300'
            }`}>
              {timeline.task.status}
            </span>
            {timeline.task.workflow_id && (
              <a
                href={`http://localhost:8088/namespaces/default/workflows/${timeline.task.workflow_id}`}
                target="_blank"
                className="text-blue-400 hover:underline text-xs"
              >
                Temporal UI &#x2197;
              </a>
            )}
            {/* Continue button — shown for completed or failed tasks */}
            {(timeline.task.status === 'completed' || timeline.task.status === 'failed') && (
              <button
                onClick={() => setShowContinue(!showContinue)}
                className="ml-auto px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium rounded transition-colors"
              >
                {showContinue ? 'Cancel' : '♻️ Continue / Iterate'}
              </button>
            )}
          </div>
          {/* Continue panel */}
          {showContinue && (
            <div className="mt-4 bg-indigo-900/20 border border-indigo-500/30 rounded-lg p-4">
              <label className="block text-sm font-medium text-indigo-300 mb-2">
                Follow-up instructions
              </label>
              <textarea
                value={followUp}
                onChange={(e) => setFollowUp(e.target.value)}
                placeholder="e.g. The chart doesn't auto-refresh. Add JavaScript polling to update the chart every 5 seconds without a page reload."
                className="w-full bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-y"
                rows={3}
              />
              <div className="flex items-center gap-3 mt-3">
                <button
                  onClick={handleContinue}
                  disabled={continuing || !followUp.trim()}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm font-medium rounded transition-colors"
                >
                  {continuing ? 'Starting…' : '🚀 Continue Task'}
                </button>
                <span className="text-xs text-gray-500">
                  The agent will resume with all previous deliverables and installed packages.
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Current State */}
        {currentState && (
          <div className="bg-gradient-to-r from-blue-900/20 to-purple-900/20 border border-blue-500/30 rounded-lg p-5 mb-6">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-center">
              <div className="bg-gray-800/50 rounded p-3">
                <div className="text-xs text-gray-500 uppercase tracking-wider">Status</div>
                <div className="text-lg font-bold mt-1">{currentState.status || timeline.task.status}</div>
              </div>
              <div className="bg-gray-800/50 rounded p-3">
                <div className="text-xs text-gray-500 uppercase tracking-wider">Iterations</div>
                <div className="text-lg font-bold text-blue-400 mt-1">{outputs.length}</div>
              </div>
              <div className="bg-gray-800/50 rounded p-3">
                <div className="text-xs text-gray-500 uppercase tracking-wider">Image Version</div>
                <div className="text-lg font-bold text-purple-400 mt-1">v{currentState.current_image_version}</div>
              </div>
              <div className="bg-gray-800/50 rounded p-3">
                <div className="text-xs text-gray-500 uppercase tracking-wider">Pending</div>
                <div className="text-lg font-bold text-yellow-400 mt-1">{currentState.pending_approvals}</div>
              </div>
              <div className="bg-gray-800/50 rounded p-3">
                <div className="text-xs text-gray-500 uppercase tracking-wider">Model</div>
                <div className="text-sm font-medium text-green-400 mt-1 truncate">
                  {outputs[0]?.model_used || '---'}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Description */}
        <div className="bg-gray-800/60 rounded-lg p-4 mb-6 border border-gray-700">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Task Description</div>
          <p className="text-gray-200">{timeline.task.description || 'No description'}</p>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-700 mb-6 gap-1">
          {(['outputs', 'audit', 'timeline'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-5 py-2.5 text-sm font-medium capitalize rounded-t-lg transition-colors ${
                activeTab === tab
                  ? 'bg-gray-800 text-white border-b-2 border-blue-500'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50'
              }`}
            >
              {tab === 'outputs' ? `\u{1F4CA} Outputs (${outputs.length})` :
               tab === 'audit' ? `\u{1F50D} Audit Log` :
               '\u{1F4C5} Timeline'}
            </button>
          ))}
        </div>

        {/* TAB: Outputs */}
        {activeTab === 'outputs' && (
          <div className="space-y-4">
            {outputs.length === 0 ? (
              <div className="bg-gray-800/40 rounded-lg p-8 text-center text-gray-500">
                <div className="text-4xl mb-3">{'\u{1F4ED}'}</div>
                <p>No outputs yet. Start the task to see agent execution results.</p>
              </div>
            ) : (
              outputs.map((o) => {
                const isExpanded = expandedOutput === o.iteration;
                const preview = extractLlmPreview(o);
                const hasError = !!o.error;
                const isDone = o.completed === 'true';
                const hasCap = o.capability_requested === 'true';
                const deliverableCount = o.deliverables ? Object.keys(o.deliverables).length : 0;

                return (
                  <div
                    key={o.id}
                    className={`rounded-lg border transition-colors ${
                      hasError ? 'border-red-500/40 bg-red-900/10' :
                      isDone   ? 'border-green-500/40 bg-green-900/10' :
                      hasCap   ? 'border-yellow-500/40 bg-yellow-900/10' :
                      'border-gray-700 bg-gray-800/50'
                    }`}
                  >
                    {/* Header row */}
                    <button
                      onClick={() => setExpandedOutput(isExpanded ? null : o.iteration)}
                      className="w-full text-left px-5 py-3 flex items-center justify-between hover:bg-white/5 rounded-t-lg"
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-lg">
                          {hasError ? '\u274C' : isDone ? '\u2705' : hasCap ? '\u{1F510}' : '\u{1F916}'}
                        </span>
                        <span className="font-mono text-sm text-gray-300">
                          Iteration {o.iteration}
                        </span>
                        {o.model_used && (
                          <span className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">
                            {o.model_used}
                          </span>
                        )}
                        {o.duration_ms && (
                          <span className="text-xs text-gray-500">
                            {(o.duration_ms / 1000).toFixed(1)}s
                          </span>
                        )}
                        {deliverableCount > 0 && (
                          <span className="text-xs bg-emerald-900/50 text-emerald-300 px-2 py-0.5 rounded">
                            {deliverableCount} file{deliverableCount > 1 ? 's' : ''}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          hasError ? 'bg-red-900/50 text-red-300' :
                          isDone   ? 'bg-green-900/50 text-green-300' :
                          hasCap   ? 'bg-yellow-900/50 text-yellow-300' :
                          'bg-blue-900/50 text-blue-300'
                        }`}>
                          {hasError ? 'Error' : isDone ? 'Completed' : hasCap ? 'Capability' : 'Running'}
                        </span>
                        <span className="text-gray-500 text-xs">
                          {o.created_at ? new Date(o.created_at).toLocaleTimeString() : ''}
                        </span>
                        <span className="text-gray-500">{isExpanded ? '\u25BC' : '\u25B6'}</span>
                      </div>
                    </button>

                    {/* Preview (always visible when collapsed) */}
                    {!isExpanded && preview && (
                      <div className="px-5 pb-3 text-sm text-gray-400 truncate">
                        {preview.slice(0, 200)}
                      </div>
                    )}

                    {/* Expanded details */}
                    {isExpanded && (
                      <div className="border-t border-gray-700 px-5 py-4 space-y-4">
                        {/* Error */}
                        {o.error && (
                          <div className="bg-red-900/20 border border-red-500/30 rounded p-3">
                            <div className="text-xs text-red-400 uppercase font-medium mb-1">Error</div>
                            <pre className="text-sm text-red-200 whitespace-pre-wrap break-words font-mono">
                              {o.error}
                            </pre>
                          </div>
                        )}

                        {/* LLM Response Preview */}
                        {preview && (
                          <div>
                            <div className="text-xs text-gray-500 uppercase font-medium mb-1">LLM Response</div>
                            <div className="bg-gray-900 rounded p-3 text-sm text-gray-200 whitespace-pre-wrap">
                              {preview}
                            </div>
                          </div>
                        )}

                        {/* Agent Logs */}
                        {o.agent_logs && (
                          <div>
                            <div className="text-xs text-gray-500 uppercase font-medium mb-1">Agent Logs</div>
                            <pre className="bg-gray-900 rounded p-3 text-xs text-gray-400 whitespace-pre-wrap max-h-80 overflow-y-auto font-mono">
                              {o.agent_logs}
                            </pre>
                          </div>
                        )}

                        {/* Deliverable Files */}
                        {o.deliverables && Object.keys(o.deliverables).length > 0 && (
                          <div>
                            <div className="flex items-center justify-between mb-2">
                              <div className="text-xs text-gray-500 uppercase font-medium">
                                {'\u{1F4E6}'} Deliverable Files ({Object.keys(o.deliverables).length})
                              </div>
                              {Object.keys(o.deliverables).length > 1 && (
                                <button
                                  onClick={() => downloadAllDeliverables(o.deliverables!)}
                                  className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1 transition-colors"
                                >
                                  ⬇ Download All
                                </button>
                              )}
                            </div>
                            <div className="space-y-3">
                              {Object.entries(o.deliverables).map(([filename, content]) => {
                                const binary = isBinaryContent(content);
                                const isImage = binary && /\.(png|jpg|jpeg|gif|webp|bmp|svg)$/i.test(filename);
                                return (
                                <div key={filename} className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
                                  <div className="flex items-center justify-between px-3 py-2 bg-gray-800/80 border-b border-gray-700">
                                    <div className="flex items-center gap-2">
                                      <span className="text-sm">{getFileIcon(filename, binary)}</span>
                                      <span className="text-sm font-mono text-emerald-400">{filename}</span>
                                    </div>
                                    <div className="flex items-center gap-3">
                                      <span className="text-xs text-gray-500">
                                        {binary ? formatFileSize(content, true) : `${content.split('\n').length} lines`}
                                      </span>
                                      <button
                                        onClick={() => downloadFile(filename, content)}
                                        className="text-xs px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-white transition-colors flex items-center gap-1"
                                        title={`Download ${filename}`}
                                      >
                                        ⬇ Download
                                      </button>
                                    </div>
                                  </div>
                                  {binary ? (
                                    isImage ? (
                                      <div className="p-3 flex justify-center bg-gray-950">
                                        <img
                                          src={`data:${getMimeType(filename)};base64,${content.slice(7)}`}
                                          alt={filename}
                                          className="max-h-64 max-w-full rounded"
                                        />
                                      </div>
                                    ) : (
                                      <div className="p-3 text-xs text-gray-500 italic text-center">
                                        Binary file — {formatFileSize(content, true)} — click Download to save
                                      </div>
                                    )
                                  ) : (
                                    <pre className="p-3 text-xs text-gray-300 whitespace-pre-wrap overflow-x-auto max-h-64 overflow-y-auto font-mono">
                                      {content}
                                    </pre>
                                  )}
                                </div>
                                );
                              })}
                            </div>
                          </div>
                        )}

                        {/* Raw JSON toggle */}
                        <div>
                          <button
                            onClick={() => setShowRawJson(showRawJson === o.iteration ? null : o.iteration)}
                            className="text-xs text-blue-400 hover:text-blue-300"
                          >
                            {showRawJson === o.iteration ? '\u25BC Hide' : '\u25B6 Show'} Raw JSON
                          </button>
                          {showRawJson === o.iteration && o.raw_result && (
                            <pre className="mt-2 bg-gray-900 rounded p-3 text-xs text-gray-400 whitespace-pre-wrap max-h-64 overflow-y-auto font-mono">
                              {JSON.stringify(o.raw_result, null, 2)}
                            </pre>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* TAB: Audit Log */}
        {activeTab === 'audit' && (
          <div className="space-y-6">
            {outputs.length === 0 ? (
              <div className="bg-gray-800/40 rounded-lg p-8 text-center text-gray-500">
                <div className="text-4xl mb-3">{'\u{1F50D}'}</div>
                <p>No audit data yet. Start the task to see detailed execution logs.</p>
              </div>
            ) : (
              outputs.map((o) => {
                const raw = o.raw_result || {};
                const interactions = raw.llm_interactions || [];
                const meta = raw._temporal_metadata || {};
                const capability = raw.capability || null;
                const isDone = o.completed === 'true';
                const hasCap = o.capability_requested === 'true';
                const hasError = !!o.error;

                // Parse wrapper log into steps
                const parseWrapperSteps = (logs: string | null): string[] => {
                  if (!logs) return [];
                  const steps: string[] = [];
                  for (const line of logs.split('\n')) {
                    const trimmed = line.trim();
                    if (!trimmed || trimmed.startsWith('=') || trimmed.startsWith('{') || trimmed.startsWith('"')) continue;
                    if (trimmed.startsWith('📋') || trimmed.startsWith('🔄') || trimmed.startsWith('🤖') ||
                        trimmed.startsWith('🌐') || trimmed.startsWith('🔀') || trimmed.startsWith('📥') ||
                        trimmed.startsWith('✅') || trimmed.startsWith('🚀') || trimmed.startsWith('❌') ||
                        trimmed.startsWith('🔑') || trimmed.startsWith('📦') || trimmed.startsWith('📤') ||
                        trimmed.startsWith('🦞') || trimmed.startsWith('📊') || trimmed.startsWith('⚠️')) {
                      steps.push(trimmed);
                    } else if (trimmed.startsWith('Router URL:') || trimmed.startsWith('Model:') ||
                               trimmed.startsWith('Config:') || trimmed.startsWith('Binary:')) {
                      steps.push('   ' + trimmed);
                    }
                  }
                  return steps;
                };

                const wrapperSteps = parseWrapperSteps(o.agent_logs);

                // Calculate total tokens across all interactions
                const totalTokens = interactions.reduce((sum: number, inter: any) => {
                  return sum + (inter.response?.usage?.total_tokens || 0);
                }, 0);

                return (
                  <div key={o.id} className="rounded-xl border border-gray-700 bg-gray-800/30 overflow-hidden">
                    {/* Iteration header */}
                    <div className={`px-5 py-3 border-b border-gray-700 flex items-center justify-between ${
                      hasError ? 'bg-red-900/15' : isDone ? 'bg-emerald-900/15' : hasCap ? 'bg-amber-900/15' : 'bg-blue-900/15'
                    }`}>
                      <div className="flex items-center gap-3">
                        <span className="text-lg">
                          {hasError ? '❌' : isDone ? '✅' : hasCap ? '🔐' : '🤖'}
                        </span>
                        <div>
                          <span className="font-semibold text-white">Iteration {o.iteration}</span>
                          <span className="text-sm text-gray-400 ml-3">
                            {hasError ? 'Failed' : isDone ? 'Completed' : hasCap ? 'Capability Requested' : 'Running'}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-4 text-xs text-gray-500">
                        {o.model_used && <span className="bg-gray-700 px-2 py-0.5 rounded text-gray-300">{o.model_used}</span>}
                        {o.duration_ms && <span>{(o.duration_ms / 1000).toFixed(1)}s</span>}
                        {totalTokens > 0 && <span>{totalTokens.toLocaleString()} tokens</span>}
                        {o.created_at && <span>{new Date(o.created_at).toLocaleTimeString()}</span>}
                      </div>
                    </div>

                    <div className="p-5 space-y-5">
                      {/* Metadata */}
                      {meta.image && (
                        <div>
                          <div className="text-xs text-gray-500 uppercase font-medium tracking-wider mb-2">Container Environment</div>
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Image</div>
                              <div className="text-xs font-mono text-gray-300 truncate">{meta.image}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Model</div>
                              <div className="text-xs font-mono text-gray-300">{o.model_used || '—'}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Duration</div>
                              <div className="text-xs font-mono text-gray-300">{o.duration_ms ? `${(o.duration_ms / 1000).toFixed(1)}s` : '—'}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Timestamp</div>
                              <div className="text-xs font-mono text-gray-300">{meta.timestamp || '—'}</div>
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Agent Wrapper Steps */}
                      {wrapperSteps.length > 0 && (
                        <div>
                          <div className="text-xs text-gray-500 uppercase font-medium tracking-wider mb-2">Agent Wrapper Execution</div>
                          <div className="bg-gray-900 rounded-lg border border-gray-700 p-3 space-y-0.5">
                            {wrapperSteps.map((step, i) => (
                              <div key={i} className={`text-xs font-mono ${
                                step.trim().startsWith('✅') ? 'text-emerald-400' :
                                step.trim().startsWith('❌') ? 'text-red-400' :
                                step.trim().startsWith('⚠️') ? 'text-amber-400' :
                                step.trim().startsWith('🚀') ? 'text-blue-400' :
                                step.trim().startsWith('📦') ? 'text-purple-400' :
                                step.startsWith('   ') ? 'text-gray-600 pl-4' :
                                'text-gray-400'
                              }`}>
                                {step}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* LLM Interactions */}
                      {interactions.length > 0 && (
                        <div>
                          <div className="text-xs text-gray-500 uppercase font-medium tracking-wider mb-2">
                            LLM Interactions ({interactions.length} turn{interactions.length > 1 ? 's' : ''})
                          </div>
                          <div className="space-y-3">
                            {interactions.map((inter: any, idx: number) => {
                              const resp = inter.response || {};
                              const toolCalls = resp.tool_calls || [];
                              const usage = resp.usage || {};
                              const reqInfo = inter.request || {};
                              return (
                                <div key={idx} className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
                                  {/* Turn header */}
                                  <div className="flex items-center justify-between px-4 py-2.5 bg-gray-800/50 border-b border-gray-700">
                                    <div className="flex items-center gap-2">
                                      <span className="text-xs font-bold text-indigo-400">Turn {inter.turn || idx + 1}</span>
                                      <span className="text-xs text-gray-600">via {inter.provider || 'unknown'}</span>
                                      {inter.streaming && <span className="text-xs bg-blue-500/10 text-blue-400 px-1.5 py-0.5 rounded">streaming</span>}
                                    </div>
                                    <div className="flex items-center gap-3 text-xs text-gray-500">
                                      {usage.prompt_tokens && <span>in: {usage.prompt_tokens.toLocaleString()}</span>}
                                      {usage.completion_tokens && <span>out: {usage.completion_tokens.toLocaleString()}</span>}
                                      {usage.total_tokens && <span className="text-gray-400 font-medium">Σ {usage.total_tokens.toLocaleString()}</span>}
                                      {inter.timestamp && <span>{new Date(inter.timestamp).toLocaleTimeString()}</span>}
                                    </div>
                                  </div>

                                  {/* Request info */}
                                  <div className="px-4 py-2 border-b border-gray-800">
                                    <div className="flex items-center gap-2 text-xs">
                                      <span className="text-gray-600">Request:</span>
                                      <span className="text-gray-400">{reqInfo.msg_count || '?'} messages</span>
                                      {reqInfo.roles && (
                                        <span className="text-gray-600">
                                          [{reqInfo.roles.join(' → ')}]
                                        </span>
                                      )}
                                      {reqInfo.tool_results && reqInfo.tool_results.length > 0 && (
                                        <span className="text-purple-400">
                                          + {reqInfo.tool_results.length} tool result{reqInfo.tool_results.length > 1 ? 's' : ''}
                                        </span>
                                      )}
                                    </div>
                                  </div>

                                  {/* Tool calls */}
                                  {toolCalls.length > 0 && (
                                    <div className="px-4 py-2.5">
                                      <div className="text-xs text-gray-600 mb-2">Tool Calls:</div>
                                      <div className="space-y-2">
                                        {toolCalls.map((tc: any, tci: number) => {
                                          let argsPreview = '';
                                          try {
                                            const parsed = typeof tc.arguments === 'string' ? JSON.parse(tc.arguments) : tc.arguments;
                                            if (parsed && typeof parsed === 'object') {
                                              if (parsed.file_path) argsPreview = parsed.file_path;
                                              else if (parsed.command) argsPreview = parsed.command;
                                              else argsPreview = Object.keys(parsed).join(', ');
                                            } else {
                                              argsPreview = String(tc.arguments || '').substring(0, 80);
                                            }
                                          } catch {
                                            argsPreview = String(tc.arguments || '').substring(0, 80);
                                          }
                                          return (
                                            <div key={tci} className="flex items-start gap-2">
                                              <span className="text-xs font-mono bg-indigo-500/15 text-indigo-400 px-1.5 py-0.5 rounded shrink-0">
                                                {tc.name}
                                              </span>
                                              <span className="text-xs text-gray-500 font-mono truncate">
                                                {argsPreview}
                                              </span>
                                            </div>
                                          );
                                        })}
                                      </div>
                                    </div>
                                  )}

                                  {/* Tool results from request */}
                                  {reqInfo.tool_results && reqInfo.tool_results.length > 0 && (
                                    <div className="px-4 py-2.5 border-t border-gray-800">
                                      <div className="text-xs text-gray-600 mb-2">Tool Results:</div>
                                      <div className="space-y-1.5">
                                        {reqInfo.tool_results.map((tr: any, tri: number) => (
                                          <div key={tri} className="text-xs">
                                            <span className="font-mono text-gray-500">{tr.tool_call_id?.substring(0, 20)}...</span>
                                            <div className="bg-gray-950 rounded p-2 mt-1 text-gray-400 font-mono whitespace-pre-wrap max-h-24 overflow-y-auto">
                                              {String(tr.content || '').substring(0, 300)}{String(tr.content || '').length > 300 ? '...' : ''}
                                            </div>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  )}

                                  {/* Finish reason */}
                                  {resp.finish_reason && toolCalls.length === 0 && (
                                    <div className="px-4 py-2 border-t border-gray-800">
                                      <span className="text-xs text-gray-600">Response: </span>
                                      <span className={`text-xs ${resp.finish_reason === 'stop' ? 'text-emerald-400' : 'text-amber-400'}`}>
                                        {resp.finish_reason}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* OpenClaw Agent Meta (fallback when no llm_interactions) */}
                      {interactions.length === 0 && raw.meta?.agentMeta && (
                        <div>
                          <div className="text-xs text-gray-500 uppercase font-medium tracking-wider mb-2">Agent Execution Summary</div>
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Provider</div>
                              <div className="text-xs font-mono text-gray-300">{raw.meta.agentMeta.provider || '—'}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Model</div>
                              <div className="text-xs font-mono text-gray-300">{raw.meta.agentMeta.model || '—'}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Input Tokens</div>
                              <div className="text-xs font-mono text-indigo-400">{(raw.meta.agentMeta.usage?.input || 0).toLocaleString()}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded-lg p-2.5">
                              <div className="text-xs text-gray-600">Output Tokens</div>
                              <div className="text-xs font-mono text-emerald-400">{(raw.meta.agentMeta.usage?.output || 0).toLocaleString()}</div>
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Capability Request */}
                      {capability && (
                        <div>
                          <div className="text-xs text-gray-500 uppercase font-medium tracking-wider mb-2">Capability Request</div>
                          <div className="bg-amber-900/10 border border-amber-500/20 rounded-lg p-3">
                            <div className="flex items-center gap-3 mb-1">
                              <span className="text-xs font-mono bg-amber-500/15 text-amber-400 px-2 py-0.5 rounded">{capability.type}</span>
                              <span className="text-sm text-white font-medium">{capability.resource}</span>
                            </div>
                            <p className="text-xs text-gray-400">{capability.justification}</p>
                          </div>
                        </div>
                      )}

                      {/* Deliverables */}
                      {o.deliverables && Object.keys(o.deliverables).length > 0 && (
                        <div>
                          <div className="flex items-center justify-between mb-2">
                            <div className="text-xs text-gray-500 uppercase font-medium tracking-wider">
                              Deliverables ({Object.keys(o.deliverables).length} files)
                            </div>
                            {Object.keys(o.deliverables).length > 1 && (
                              <button
                                onClick={() => downloadAllDeliverables(o.deliverables!)}
                                className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1 transition-colors"
                              >
                                ⬇ Download All
                              </button>
                            )}
                          </div>
                          <div className="space-y-2">
                            {Object.entries(o.deliverables).map(([filename, content]) => {
                              const raw = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
                              const binary = isBinaryContent(raw);
                              const isImage = binary && /\.(png|jpg|jpeg|gif|webp|bmp|svg)$/i.test(filename);
                              return (
                              <div key={filename} className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
                                <div className="flex items-center justify-between px-3 py-2 bg-gray-800/80 border-b border-gray-700">
                                  <div className="flex items-center gap-2">
                                    <span className="text-sm">{getFileIcon(filename, binary)}</span>
                                    <span className="text-xs font-mono text-emerald-400">{filename}</span>
                                  </div>
                                  <div className="flex items-center gap-3">
                                    <span className="text-xs text-gray-500">
                                      {binary ? formatFileSize(raw, true) : `${raw.split('\n').length} lines`}
                                    </span>
                                    <button
                                      onClick={() => downloadFile(filename, raw)}
                                      className="text-xs px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-white transition-colors flex items-center gap-1"
                                      title={`Download ${filename}`}
                                    >
                                      ⬇ Download
                                    </button>
                                  </div>
                                </div>
                                {binary ? (
                                  isImage ? (
                                    <div className="p-3 flex justify-center bg-gray-950">
                                      <img
                                        src={`data:${getMimeType(filename)};base64,${raw.slice(7)}`}
                                        alt={filename}
                                        className="max-h-48 max-w-full rounded"
                                      />
                                    </div>
                                  ) : (
                                    <div className="p-3 text-xs text-gray-500 italic text-center">
                                      Binary file — {formatFileSize(raw, true)} — click Download to save
                                    </div>
                                  )
                                ) : (
                                  <pre className="p-3 text-xs text-gray-300 whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto font-mono">
                                    {raw}
                                  </pre>
                                )}
                              </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Error */}
                      {o.error && (
                        <div>
                          <div className="text-xs text-gray-500 uppercase font-medium tracking-wider mb-2">Error</div>
                          <pre className="bg-red-900/20 border border-red-500/30 rounded-lg p-3 text-xs text-red-300 font-mono whitespace-pre-wrap">
                            {o.error}
                          </pre>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}

            {/* Token Usage Summary */}
            {outputs.length > 0 && (
              <div className="rounded-xl border border-gray-700 bg-gray-800/30 p-5">
                <div className="text-xs text-gray-500 uppercase font-medium tracking-wider mb-3">Token Usage Summary</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                    <div className="text-lg font-bold text-white">
                      {outputs.reduce((sum, o) => sum + (o.raw_result?.llm_interactions || []).length, 0)}
                    </div>
                    <div className="text-xs text-gray-500">Total LLM Calls</div>
                  </div>
                  <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                    <div className="text-lg font-bold text-indigo-400">
                      {outputs.reduce((sum, o) => {
                        return sum + (o.raw_result?.llm_interactions || []).reduce((s: number, i: any) => s + (i.response?.usage?.prompt_tokens || 0), 0);
                      }, 0).toLocaleString()}
                    </div>
                    <div className="text-xs text-gray-500">Input Tokens</div>
                  </div>
                  <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                    <div className="text-lg font-bold text-emerald-400">
                      {outputs.reduce((sum, o) => {
                        return sum + (o.raw_result?.llm_interactions || []).reduce((s: number, i: any) => s + (i.response?.usage?.completion_tokens || 0), 0);
                      }, 0).toLocaleString()}
                    </div>
                    <div className="text-xs text-gray-500">Output Tokens</div>
                  </div>
                  <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                    <div className="text-lg font-bold text-amber-400">
                      {outputs.reduce((sum, o) => sum + (o.duration_ms || 0), 0) > 0
                        ? `${(outputs.reduce((sum, o) => sum + (o.duration_ms || 0), 0) / 1000).toFixed(1)}s`
                        : '—'}
                    </div>
                    <div className="text-xs text-gray-500">Total Duration</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* TAB: Timeline */}
        {activeTab === 'timeline' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            {/* Timeline events */}
            <div>
              <h2 className="text-xl font-bold mb-4">Execution Timeline</h2>
              <div className="space-y-4">
                {timeline.timeline.map((event: any, index: number) => (
                  <div key={index} className={`border-l-4 ${getEventColor(event.event)} pl-4 py-2`}>
                    <div className="flex items-start">
                      <span className="text-2xl mr-3">{getEventIcon(event.event)}</span>
                      <div className="flex-1">
                        <p className="font-semibold">{event.description}</p>
                        <p className="text-sm text-gray-400">
                          {new Date(event.timestamp).toLocaleString()}
                        </p>
                        {event.data.justification && (
                          <p className="text-sm text-gray-300 mt-1">
                            <span className="font-medium">Justification:</span> {event.data.justification}
                          </p>
                        )}
                        {event.data.notes && (
                          <p className="text-sm text-gray-300 mt-1">
                            <span className="font-medium">Notes:</span> {event.data.notes}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Right column: capability requests + dockerfiles */}
            <div className="space-y-8">
              {/* Capability Requests */}
              <div>
                <h2 className="text-xl font-bold mb-4">Capability Requests</h2>
                {timeline.capability_requests.length > 0 ? (
                  <div className="space-y-3">
                    {timeline.capability_requests.map((req: any) => (
                      <div key={req.id} className="bg-gray-800 rounded-lg p-4">
                        <div className="flex justify-between items-start mb-2">
                          <span className="font-semibold">{req.type}: {req.resource}</span>
                          <span className={`px-2 py-1 rounded text-xs ${
                            req.status === 'approved' ? 'bg-green-900 text-green-300' :
                            req.status === 'denied' ? 'bg-red-900 text-red-300' :
                            'bg-yellow-900 text-yellow-300'
                          }`}>
                            {req.status}
                          </span>
                        </div>
                        <p className="text-sm text-gray-400">{req.justification}</p>
                        <div className="text-xs text-gray-500 mt-2">
                          Requested: {new Date(req.requested_at).toLocaleString()}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-gray-500 text-sm">No capability requests yet.</p>
                )}
              </div>

              {/* Agent images */}
              <div>
                <h2 className="text-xl font-bold mb-4">Agent Images ({timeline.image_versions})</h2>
                {timeline.dockerfiles.length > 0 ? (
                  <div className="space-y-3">
                    {timeline.dockerfiles.map((df: any) => (
                      <div key={df.version} className="bg-gray-800 rounded-lg p-4">
                        <button
                          onClick={() => setSelectedDockerfile(selectedDockerfile === df.version ? null : df.version)}
                          className="w-full text-left flex justify-between items-center"
                        >
                          <div>
                            <span className="font-semibold">Version: {df.version}</span>
                            <p className="text-sm text-gray-400">{df.lines} lines</p>
                          </div>
                          <span className="text-gray-400">{selectedDockerfile === df.version ? '\u25BC' : '\u25B6'}</span>
                        </button>
                        {selectedDockerfile === df.version && (
                          <pre className="mt-3 bg-gray-900 p-4 rounded text-xs overflow-x-auto"><code>{df.content}</code></pre>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="bg-gray-800 rounded-lg p-4 text-gray-400 text-sm">
                    Using base image: <code className="text-blue-400">openclaw-agent:openclaw</code>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="mt-8 flex items-center gap-4 text-sm">
          <a href="/tasks" className="text-indigo-400 hover:text-indigo-300">← Back to Tasks</a>
          {timeline.task.workflow_id && (
            <a
              href={`http://localhost:8088/namespaces/default/workflows/${timeline.task.workflow_id}`}
              target="_blank"
              className="text-gray-500 hover:text-gray-300"
            >
              Open in Temporal UI ↗
            </a>
          )}
        </div>
    </div>
  );
}
