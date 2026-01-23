-- DDL of tables and stages needed for the document agent
use role <% ctx.env.role %>;
use schema <% ctx.env.database %>.<% ctx.env.schema %>;

-- Stage for raw document files
create stage if not exists documents directory = (enable = true) encryption = (type = 'SNOWFLAKE_SSE');

-- Table containing document modified timestamps and ground-truth metadata
create table if not exists document_metadata (
    source_uri string,
    modified_at_utc timestamp_ntz,
    metadata string
) change_tracking = true;

-- Underlying table for metadata search
create table if not exists enhanced_metadata (
    source_uri string,
    enhanced_metadata string
) change_tracking = true;

-- Underlying tables for content search
create table if not exists parsed_documents (
    source_uri string,
    parsed_content string
) change_tracking = true;
create table if not exists document_chunks (
    source_uri string,
    contextualized_chunk string
) change_tracking = true;
