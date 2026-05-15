UPDATE businesses
SET settings = COALESCE(settings, '{}'::jsonb)
             || jsonb_build_object(
                  'out_of_zone_delivery_contacts',
                  jsonb_build_array(
                    jsonb_build_object('city', 'Ipiales', 'phone', '3239609582')
                  )
                )
WHERE id = '44488756-473b-46d2-a907-9f579e98ecfd';
