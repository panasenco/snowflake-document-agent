-- Cortex-related access provisioning for the document agent role
use role <% ctx.env.role %>;
use schema <% ctx.env.database %>.<% ctx.env.schema %>;

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
create agent if not exists snowflake_intelligence.agents.<% ctx.env.agent_name %>
  comment = '<% ctx.env.agent_description %>'
  profile = '{"avatar":  "<% ctx.env.agent_icon %>", "color": "<% ctx.env.agent_color %>"}'
  from specification
-- alter agent snowflake_intelligence.agents.<% ctx.env.agent_name %> modify live version set specification =
  $$
  <% ctx.env.agent_specification_without_tools %>

  tools:
    - tool_spec:
        type: "cortex_search"
        name: "search_document_contents"
        description: >
          Search Pacific Life operations documents (procedures, forms, product details, and guidelines).
          Uses hybrid semantic and keyword matching - use natural-language phrases, not just single keywords.
          A single document may span multiple chunks; search with varied queries to gather all relevant chunks.
          Each result is formatted as:
          ```
          Document filename
          Chunk N / Total chunks in document

          Chunk content
          ```

  tool_resources:
    search_document_contents:
      id_column: "SOURCE_URI"
      max_results: <% ctx.env.agent_content_max_results %>
      search_service: "<% ctx.env.database %>.<% ctx.env.schema %>.<% ctx.env.agent_name %>_SEARCH_CONTENTS"
      title_column: "DISPLAY_NAME"
  $$;
