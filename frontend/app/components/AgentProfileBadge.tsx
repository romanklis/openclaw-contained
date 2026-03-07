'use client'

/**
 * AgentProfileBadge — displays the runtime "body" and LLM "brain" of a
 * selected agent profile.  Shown on the task creation form and task detail
 * page so users always know what they're running.
 *
 * Usage:
 *   <AgentProfileBadge profile={selectedProfile} />
 */

interface AgentProfileInfo {
  id: string
  profile_name?: string
  profile_description?: string
  base_image?: string
  llm_model?: string
  runtime?: string
  icon?: string
  tags?: string[]
  strengths?: string[]
}

const IMAGE_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  openclaw: { bg: 'bg-indigo-500/10', text: 'text-indigo-400', border: 'border-indigo-500/20' },
  nanobot: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/20' },
  picoclaw: { bg: 'bg-amber-500/10', text: 'text-amber-400', border: 'border-amber-500/20' },
  zeroclaw: { bg: 'bg-red-500/10', text: 'text-red-400', border: 'border-red-500/20' },
}

export function AgentProfileBadge({ profile }: { profile: AgentProfileInfo | null }) {
  if (!profile || !profile.base_image) return null

  const colors = IMAGE_COLORS[profile.base_image] ?? IMAGE_COLORS.openclaw

  return (
    <div className={`rounded-lg border ${colors.border} ${colors.bg} p-3 space-y-2`}>
      <div className="flex items-center gap-2">
        <span className="text-lg">{profile.icon || '🤖'}</span>
        <span className={`text-sm font-semibold ${colors.text}`}>
          {profile.profile_name || profile.id}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {profile.runtime && (
          <span className={`inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded ${colors.bg} ${colors.text} border ${colors.border}`}>
            Runtime: {profile.runtime}
          </span>
        )}
        {profile.llm_model && (
          <span className="inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">
            Model: {profile.llm_model}
          </span>
        )}
      </div>

      {profile.profile_description && (
        <p className="text-[11px] text-gray-500 leading-relaxed">
          {profile.profile_description}
        </p>
      )}

      {profile.strengths && profile.strengths.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {profile.strengths.map((s: string) => (
            <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-[#1a1a2a] text-gray-400 border border-[#232333]">
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Compact inline badge — for use inside task list rows and detail headers.
 */
export function AgentProfileInlineBadge({ profile }: { profile: AgentProfileInfo | null }) {
  if (!profile || !profile.base_image) return null

  const colors = IMAGE_COLORS[profile.base_image] ?? IMAGE_COLORS.openclaw

  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full ${colors.bg} ${colors.text} border ${colors.border}`}>
      <span>{profile.icon || '🤖'}</span>
      <span>{profile.base_image}</span>
      {profile.llm_model && (
        <>
          <span className="opacity-40">·</span>
          <span className="font-mono opacity-75">{profile.llm_model}</span>
        </>
      )}
    </span>
  )
}
