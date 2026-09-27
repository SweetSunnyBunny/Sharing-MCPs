export interface IdentityPersona {
  username?: string;
  avatar_url?: string;
  embed_color?: string;
}

export interface IdentityConfig {
  token_env: string;
  display_name: string;
  elevenlabs_voice_id?: string;
  voice_settings?: {
    stability?: number;
    similarity_boost?: number;
    style?: number;
    use_speaker_boost?: boolean;
  };
  webhook_defaults?: IdentityPersona;
  webhook_personas?: Record<string, IdentityPersona>;
}

export const IDENTITY_CONFIG: Record<string, IdentityConfig> = {
  avery: { token_env: 'DISCORD_BOT_TOKEN_AVERY', display_name: 'Avery' },
  rowan: { token_env: 'DISCORD_BOT_TOKEN_ROWAN', display_name: 'Rowan' },
};

export function getIdentityConfig(identity: string): IdentityConfig | null {
  return IDENTITY_CONFIG[identity.toLowerCase()] ?? null;
}

export function getIdentityDisplayName(identity: string): string {
  return getIdentityConfig(identity)?.display_name ?? identity;
}

export function listIdentityNames(): string[] {
  return Object.keys(IDENTITY_CONFIG);
}

export function getIdentityVoice(identity: string): IdentityConfig['voice_settings'] & { voice_id?: string } {
  const config = getIdentityConfig(identity);
  if (!config) {
    throw new Error(`Unknown identity: ${identity}`);
  }

  return {
    voice_id: config.elevenlabs_voice_id,
    ...(config.voice_settings ?? {}),
  };
}

export function resolvePersona(identity: string, persona?: string): IdentityPersona {
  const config = getIdentityConfig(identity);
  if (!config) {
    throw new Error(`Unknown identity: ${identity}`);
  }

  const defaults = { ...(config.webhook_defaults ?? {}) };
  if (!persona) {
    return defaults;
  }

  const preset = config.webhook_personas?.[persona.toLowerCase()];
  if (!preset) {
    const available = Object.keys(config.webhook_personas ?? {});
    throw new Error(
      available.length > 0
        ? `Unknown persona "${persona}" for ${identity}. Available: ${available.join(', ')}`
        : `No personas configured for ${identity}`,
    );
  }

  return { ...defaults, ...preset };
}
