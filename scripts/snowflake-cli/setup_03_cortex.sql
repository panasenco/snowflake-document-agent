-- Cortex-related access provisioning for the document agent role
use role <% ctx.env.admin_role %>;
grant database role snowflake.cortex_user to role <% ctx.env.role %>;
grant application role snowflake.ai_observability_events_lookup to role <% ctx.env.role %>;
grant create agent on schema <% ctx.env.database %>.<% ctx.env.schema %> to role <% ctx.env.role %>;
