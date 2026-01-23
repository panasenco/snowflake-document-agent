-- Cortex-related access provisioning for the document agent role
use role <% ctx.env.role %>;
use schema <% ctx.env.database %>.<% ctx.env.schema %>;

-- Metadata search
create cortex search service if not exists search_metadata
    on enhanced_metadata
    warehouse = <% ctx.env.warehouse %>
    target_lag = '1 minute'
    embedding_model = '<% ctx.env.embedding_model %>'
    as (select *, regexp_substr(source_uri, '[^/]*$') as filename from enhanced_metadata);

-- Content search
create cortex search service if not exists search_contents
    on contextualized_chunk
    warehouse = <% ctx.env.warehouse %>
    target_lag = '1 minute'
    embedding_model = '<% ctx.env.embedding_model %>'
    as (select *, regexp_substr(source_uri, '[^/]*$') as filename from document_chunks);

-- Agent
create or replace agent <% ctx.env.agent_name %>
  comment = '<% ctx.env.agent_description %>'
  profile = '{"display_name": "<% ctx.env.agent_display_name %>", "avatar":  "<% ctx.env.agent_icon %>", "color": "<% ctx.env.agent_color %>"}'
  from specification
  $$
  models:
    orchestration: <% ctx.env.agent_model %>

  orchestration:
    budget:
      seconds: <% ctx.env.agent_budget_seconds %>
      tokens: <% ctx.env.agent_budget_tokens %>

  instructions:
    response: "<% ctx.env.agent_instruction_response %>"
    orchestration: "<% ctx.env.agent_instruction_orchestration %>"

  tools:
    - tool_spec:
        type: "cortex_search"
        name: "search_document_metadata"
        description: "Use to locate documents by their ground-truth as well as synthetic metadata."
    - tool_spec:
        type: "cortex_search"
        name: "search_document_contents"
        description: "Use to locate contextualized document chunks by their contents."

  tool_resources:
    search_document_metadata:
      id_column: "SOURCE_URI"
      max_results: <% ctx.env.agent_metadata_max_results %>
      search_service: "< ctx.env.database %>.<% ctx.env.schema %>.search_metadata"
      title_column: "FILENAME"
      
    search_document_contents:
      id_column: "SOURCE_URI"
      max_results: <% ctx.env.agent_document_max_results %>
      search_service: "< ctx.env.database %>.<% ctx.env.schema %>.search_contents"
      title_column: "FILENAME"
  $$;
