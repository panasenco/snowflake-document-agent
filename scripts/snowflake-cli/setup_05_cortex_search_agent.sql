-- Cortex-related access provisioning for the document agent role
use role <% ctx.env.role %>;
use schema <% ctx.env.database %>.<% ctx.env.schema %>;

-- Metadata search
create cortex search service if not exists <% ctx.env.agent_name %>_search_metadata
    on generated_metadata
    warehouse = <% ctx.env.warehouse %>
    target_lag = '1 day'
    embedding_model = '<% ctx.env.search_embedding_model %>'
    initialize = on_schedule
    as select * from <% ctx.env.agent_name %>_document_metadata;

alter cortex search service <% ctx.env.agent_name %>_search_metadata suspend indexing;

-- Content search
create cortex search service if not exists <% ctx.env.agent_name %>_search_contents
    on document_chunk
    warehouse = <% ctx.env.warehouse %>
    target_lag = '1 day'
    embedding_model = '<% ctx.env.search_embedding_model %>'
    initialize = on_schedule
    as select * from <% ctx.env.agent_name %>_document_chunks;

alter cortex search service <% ctx.env.agent_name %>_search_contents suspend indexing;

-- Agent
create or replace agent snowflake_intelligence.agents.<% ctx.env.agent_name %>
  comment = '<% ctx.env.agent_description %>'
  profile = '{"avatar":  "<% ctx.env.agent_icon %>", "color": "<% ctx.env.agent_color %>"}'
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
      search_service: "<% ctx.env.database %>.<% ctx.env.schema %>.<% ctx.env.agent_name %>_SEARCH_METADATA"
      title_column: "DISPLAY_NAME"
      
    search_document_contents:
      id_column: "SOURCE_URI"
      max_results: <% ctx.env.agent_document_max_results %>
      search_service: "<% ctx.env.database %>.<% ctx.env.schema %>.<% ctx.env.agent_name %>_SEARCH_CONTENTS_BARE"
      title_column: "DISPLAY_NAME"
  $$;
