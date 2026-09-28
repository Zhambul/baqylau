<script lang="ts">
  import {
    clearExtensionSecret,
    readExtensionSecrets,
    storeExtensionSecret,
    type ExtensionSecrets,
  } from '../../api/extension-settings';
  import { messageFrom } from '../../api/client';

  let { owner }: { owner: string } = $props();

  let secrets = $state<ExtensionSecrets | null>(null);
  let drafts = $state<Record<string, string>>({});
  let failure = $state<string | null>(null);
  let busy = $state(false);

  $effect(() => {
    const controller = new AbortController();
    void readExtensionSecrets(owner, controller.signal)
      .then((reply) => {
        secrets = reply;
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) failure = messageFrom(error);
      });
    return () => {
      controller.abort();
    };
  });

  async function change(
    write: (signal: AbortSignal) => Promise<ExtensionSecrets>,
  ): Promise<void> {
    busy = true;
    failure = null;
    try {
      secrets = await write(new AbortController().signal);
    } catch (error) {
      failure = messageFrom(error);
    } finally {
      busy = false;
    }
  }

  function store(name: string): void {
    const secret = drafts[name] ?? '';
    if (secret === '') return;
    drafts[name] = '';
    void change((signal) => storeExtensionSecret(owner, name, secret, signal));
  }
</script>

{#if secrets !== null && secrets.secrets.length > 0}
  <section class="secrets" aria-label="Secrets">
    <h3>Secrets</h3>
    <p class="note">
      Values go to the system keychain. The dashboard never shows them again.
    </p>
    {#each secrets.secrets as secret (secret.name)}
      <div class="secret">
        <label for={`secret-${secret.name}`}>
          {secret.name}{secret.required ? ' (required)' : ''}
        </label>
        <span class="state">{secret.configured ? 'Set' : 'Not set'}</span>
        <input
          id={`secret-${secret.name}`}
          type="password"
          autocomplete="off"
          disabled={secrets.read_only || busy}
          bind:value={drafts[secret.name]}
        />
        <button
          type="button"
          disabled={secrets.read_only || busy || !drafts[secret.name]}
          onclick={() => {
            store(secret.name);
          }}>Save secret</button
        >
        {#if secret.configured}
          <button
            type="button"
            disabled={secrets.read_only || busy}
            onclick={() => {
              void change((signal) =>
                clearExtensionSecret(owner, secret.name, signal),
              );
            }}>Clear</button
          >
        {/if}
      </div>
    {/each}
  </section>
{/if}
{#if failure !== null}
  <p class="failure" role="alert">{failure}</p>
{/if}

<style>
  .secret {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin: 6px 0;
  }

  .state,
  .note {
    color: var(--dim);
  }

  .failure {
    color: var(--err, #e06c75);
  }
</style>
