<script lang="ts">
  import {
    ansiHtml,
    markdownHtml,
    plainTextHtml,
    sourceHtml,
    unifiedDiffHtml,
  } from './markup';
  import type { PresentationBody } from './presentation';
  import TrustedHtml from './TrustedHtml.svelte';

  let { body }: { body: PresentationBody } = $props();
</script>

{#if body.kind === 'content'}
  <div class="md">
    <TrustedHtml
      html={body.content.mediaType === 'text/markdown'
        ? markdownHtml(body.content.text)
        : plainTextHtml(body.content.text)}
    />
  </div>
{:else if body.kind === 'ansi'}
  <pre class="opo"><TrustedHtml html={ansiHtml(body.text)} /></pre>
{:else if body.kind === 'source'}
  <TrustedHtml html={sourceHtml(body.text)} />
{:else if body.kind === 'diff'}
  <TrustedHtml html={unifiedDiffHtml(body.text)} />
{:else if body.kind === 'answers'}
  {#if body.answers.length > 0}
    <div class="ansqa">
      {#each body.answers as answer (answer.questionId)}
        <div class="ansq">
          <div class="ansqh">
            <span class="ansqt">{answer.question}</span>
          </div>
          <div class="ansvs">
            {#if answer.labels.length === 0}
              <span class="ansv none">—</span>
            {:else}
              {#each answer.labels as label (label)}
                <span class="ansv">{label}</span>
              {/each}
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {:else}
    <div class="md"><p>{body.feedback ?? '—'}</p></div>
  {/if}
{:else if body.kind === 'plan-resolution'}
  <div class="md">
    {#if body.edited}<span class="pedit">edited before approval</span>{/if}
    {#if body.feedback !== null}
      <TrustedHtml html={markdownHtml(body.feedback)} />
    {/if}
  </div>
{:else if body.kind === 'skill'}
  <div class="skill-body">
    {#if body.arguments !== null}
      <div class="skill-section-label">Arguments</div>
      <div class="md">
        <TrustedHtml html={plainTextHtml(body.arguments.text)} />
      </div>
    {/if}
    {#if body.output !== null}
      <div class="skill-section-label">Output</div>
      <div class="md">
        <TrustedHtml
          html={body.output.mediaType === 'text/markdown'
            ? markdownHtml(body.output.text)
            : plainTextHtml(body.output.text)}
        />
      </div>
    {/if}
  </div>
{/if}

<style>
  .skill-section-label {
    margin: 0.55rem 0 0.2rem;
    color: rgb(152, 195, 121);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .skill-section-label:first-child {
    margin-top: 0;
  }
</style>
