-- DDL of tables and stages needed for the document agent
use role <% ctx.env.admin_role %>;
use schema <% ctx.env.database %>.<% ctx.env.schema %>;

-- Stage for raw document files
create stage if not exists <% ctx.env.agent_name %>_documents encryption = (type = 'SNOWFLAKE_SSE');
grant read, write on stage <% ctx.env.agent_name %>_documents to role <% ctx.env.role %>;

-- Underlying tables for content search
create table if not exists <% ctx.env.agent_name %>_document_text (
    source_uri string,
    document_text string(134217728)
) change_tracking = true;
grant select, insert, delete, update, truncate on table <% ctx.env.agent_name %>_document_text to role <% ctx.env.role %>;

-- Underlying table for metadata search
create table if not exists <% ctx.env.agent_name %>_document_metadata (
    source_uri string,
    display_name string,
    metadata_config_hash string,
    generated_metadata string
) change_tracking = true;
grant select, insert, delete, update, truncate on table <% ctx.env.agent_name %>_document_metadata to role <% ctx.env.role %>;

create table if not exists <% ctx.env.agent_name %>_document_chunks (
    source_uri string,
    display_name string,
    chunk_config_hash string,
    document_chunk string
) change_tracking = true;
grant select, insert, delete, update, truncate on table <% ctx.env.agent_name %>_document_chunks to role <% ctx.env.role %>;
