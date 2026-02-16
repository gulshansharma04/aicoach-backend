import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.ishaan.aicoach',
  appName: 'AI Coach',
  webDir: 'www',
  bundledWebRuntime: false,
  server: {
    // Needed only if your backend is HTTP (not recommended). Prefer HTTPS.
    // cleartext: true,
  }
};

export default config;
