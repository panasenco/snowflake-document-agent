-- DDL of tables and stages needed for the document agent
use role <% ctx.env.admin_role %>;
use schema <% ctx.env.database %>.<% ctx.env.schema %>;

-- Stage for raw document files
create stage if not exists documents encryption = (type = 'SNOWFLAKE_SSE');
grant read, write on stage documents to role <% ctx.env.role %>;

-- Underlying table for metadata search
create table if not exists document_metadata (
    source_uri string,
    display_name string,
    generated_metadata string
) change_tracking = true;
grant select, insert, delete, update, truncate on table enhanced_metadata to role <% ctx.env.role %>;

-- Underlying tables for content search
create table if not exists document_text (
    source_uri string,
    display_name string,
    document_text string
) change_tracking = true;
grant select, insert, delete, update, truncate on table document_text to role <% ctx.env.role %>;

create table if not exists document_chunks (
    source_uri string,
    display_name string,
    contextualized_chunk string
) change_tracking = true;
grant select, insert, delete, update, truncate on table document_chunks to role <% ctx.env.role %>;
