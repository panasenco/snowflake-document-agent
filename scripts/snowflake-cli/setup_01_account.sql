-- Account-level setup - run this if you get an error that an AI model is not available in your region.
use role <% ctx.env.admin_role %>;
alter account set cortex_enabled_cross_region = 'ANY_REGION';
