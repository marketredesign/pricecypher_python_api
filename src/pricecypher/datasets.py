import asyncio
import logging
import time

from datetime import datetime
from pandas import DataFrame

from pricecypher.collections import ScopeCollection, ScopeValueCollection
from pricecypher.endpoints import DatasetsEndpoint, UsersEndpoint
from pricecypher.rest import RestClient


class Datasets(object):
    """
    Datasets API. Exposes all available operations on datasets, like fetching the available datasets, listing
    the scopes within a dataset, retrieving transactions within a dataset, etc.

    :param str bearer_token: Bearer token for PriceCypher (logical) API. Needs 'read:datasets' scope.
    :param str users_base: (optional) Base URL for PriceCypher user tool API. Used to find base URL of dataset service,
        when no dss_base is provided.
        (defaults to the static default_users_base, which by default is https://users.pricecypher.com)
    :param str dss_base: (optional) Base URL for PriceCypher dataset service API.
        (defaults to dss_url property as returned for the dataset by the PriceCypher user tool API)
    :param RestClientOptions rest_options: (optional) Set any additional options for the REST client, e.g. rate-limit.
        (defaults to None)
    """

    """ 
    Default intake status to fetch when fetching transaction info, or None to let the dataset service decide. 
    NB: Can be set statically with Datasets.default_dss_intake_status = '...'
    """
    default_dss_intake_status = None

    """ Default user-tool base URL """
    default_users_base = 'https://users.pricecypher.com'

    def __init__(self, bearer_token, users_base=None, dss_base=None, rest_options=None):
        self._bearer = bearer_token
        self._users_base = users_base if users_base is not None else self.default_users_base
        self._dss_base = dss_base
        self._rest_options = rest_options
        self._all_meta = None
        self._client = RestClient(jwt=bearer_token, options=rest_options)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.close()

    async def _get_dss_base(self, dataset_id):
        """
        Get dataset service url base for the given dataset ID.
        Will be fetched from dataset META if no dss_base present.

        :param int dataset_id: Dataset ID to get base URL for.
        :return Base domain of the dataset service instance in which the given dataset is contained.
            NB: does not point to the specific dataset endpoint within the dataset service API.
        :rtype Optional[str]
        """
        if self._dss_base is not None:
            return self._dss_base

        meta = await self.get_meta(dataset_id)
        return meta.dss_url

    async def index(self):
        """
        List all available datasets the user has access to.
        Response is cached in this instance for as long as this instance lives.

        :return: list of datasets.
        :rtype list[Dataset]
        """
        if self._all_meta is None:
            self._all_meta = await UsersEndpoint(self._client, self._users_base).datasets().index()

        return self._all_meta

    async def get_meta(self, dataset_id):
        """
        Get metadata like the dataset service url and time of creation of a dataset

        :param dataset_id: Dataset to get metadata for.
        :rtype: Dataset
        """
        return next((d for d in await self.index() if d.id == dataset_id), None)

    async def get_scopes(self, dataset_id, bc_id='all'):
        """
        Get all scopes for the given dataset.

        :param int dataset_id: Dataset to retrieve scopes for.
        :param str bc_id: (optional) business cell ID.
            (defaults to 'all')
        :return: Collection of scopes for the given dataset.
        :rtype: ScopeCollection
        """
        dss_base = await self._get_dss_base(dataset_id)
        return ScopeCollection(
            await DatasetsEndpoint(self._client, dataset_id, dss_base)
            .business_cell(bc_id)
            .scopes()
            .index()
        )

    async def get_scope_values(self, dataset_id, scope_id, bc_id='all'):
        """
        Get all scopes values for the given scope within the given dataset.

        :param int dataset_id: Dataset to retrieve scope values for.
        :param int scope_id: Scope to retrieve scope values for.
        :param str bc_id: (optional) business cell ID.
            (defaults to 'all')
        :return: Collection of scope values for the given scope within the given dataset.
        :rtype: ScopeValueCollection
        """
        dss_base = await self._get_dss_base(dataset_id)
        return ScopeValueCollection(
            await DatasetsEndpoint(self._client, dataset_id, dss_base)
            .business_cell(bc_id)
            .scopes()
            .scope_values(scope_id)
        )

    async def get_transaction_summary(self, dataset_id, bc_id='all', intake_status=None):
        """
        Get a summary of the transactions. Contains the first and last date of any transaction in the dataset.

        :param int dataset_id: Dataset to retrieve summary for.
        :param str bc_id: (optional) business cell ID.
            (defaults to 'all')
        :param str intake_status: (Optional) If specified, the summary is fetched of the last intake with this status.
        :return: Summary of the transactions.
        :rtype: TransactionSummary
        """
        if intake_status is None:
            intake_status = self.default_dss_intake_status
        dss_base = await self._get_dss_base(dataset_id)
        return await DatasetsEndpoint(self._client, dataset_id, dss_base) \
            .business_cell(bc_id) \
            .transactions() \
            .summary(intake_status)

    async def get_transactions(
        self,
        dataset_id,
        aggregate,
        columns,
        start_date_time=None,
        end_date_time=None,
        bc_id='all',
        intake_status=None,
        filter_transaction_ids=None,
        page_cb=None,
    ):
        """
        Display a listing of transactions as a dataframe. The transactions can be grouped or not, using the aggregate
        parameter. The desired columns, as well as filters and aggregation methods, can be specified.

        :param int dataset_id: Dataset to retrieve transactions for.
        :param bool aggregate: If true, the transactions will be grouped on all categorical columns that have no
            aggregation method specified.
        :param list columns: Desired columns in the resulting dataframe. Each column must be a dict. Each column must
            have a `representation`, `scope_id`, or `name_dataset` specified. The following properties are optional.
                `filter`: value or list of values the resulting transactions should be filtered on.
                `aggregate`: aggregation method that should be used for this column. When aggregating and no
                    aggregation method is specified, the method that is used is determined by the underlying dataset
                    service. See dataset service documentation on how this default is determined.
                `key`: Column name to be used in the resulting dataframe. Defaults to 'scope_' appended with scope id.
        :param datetime start_date_time: When specified, only transactions at or after this date are considered.
        :param datetime end_date_time: When specified, only transactions before this date are considered.
        :param str bc_id: (optional) business cell ID.
            (defaults to 'all')
        :param str intake_status: (Optional) If specified, transactions are fetched from the last intake of this status.
        :param list filter_transaction_ids: (Optional) If specified, only transactions with these IDs are considered.
        :param page_cb: (Optional) Callback function with as input a single page of transactions,
            which gets called for each individual page.
        :return: Dataframe of transactions.
        :rtype: DataFrame
        """
        dss_base = await self._get_dss_base(dataset_id)
        # Find scopes for the provided columns.
        columns_with_scopes = await self._add_scopes(dataset_id, columns, bc_id)
        # Map each scope to the provided column key.
        scope_keys = self._find_scope_keys(columns_with_scopes)
        # Find the scope IDs that should be selected.
        select_scopes = [c['scope'].id for c in columns_with_scopes]
        # Add scope values to the columns that have a filter set.
        # TODO possible optimization: only fetch scope values that are included in the filters.
        columns_with_values = [
            self._add_scope_values(dataset_id, c, bc_id) for c in columns_with_scopes if dict.get(c, 'filter')
        ]
        # Find all scope value filters to be sent to the dataset service.
        filters = self._find_scope_value_filters(columns_with_values)
        # Find all aggregation methods to be sent to the dataset service.
        aggregation_methods = self._find_aggregation_methods(columns_with_scopes)

        # Build the request data to be sent to the dataset service.
        request_data = {
            'aggregate': aggregate,
            'select_scopes': select_scopes,
        }

        # Attach the intake status if specified
        if intake_status is None:
            intake_status = self.default_dss_intake_status
        if intake_status is not None:
            request_data['intake_status'] = intake_status

        # Attach the to-filter transaction IDs if specified
        if filter_transaction_ids is not None:
            request_data['filter_transaction_ids'] = filter_transaction_ids

        if len(filters) > 0:
            request_data['filter_scope_values'] = filters

        if len(aggregation_methods) > 0:
            request_data['aggregation_methods'] = aggregation_methods

        if isinstance(start_date_time, datetime):
            request_data['start_date_time'] = start_date_time
        elif start_date_time is not None:
            raise ValueError('start_date_time should be an instance of datetime.')

        if isinstance(end_date_time, datetime):
            request_data['end_date_time'] = end_date_time
        elif end_date_time is not None:
            raise ValueError('end_date_time should be an instance of datetime.')

        async def page_cb(page, page_nr, last_page):
            """ This callback function will be executed for each received page of transactions. """
            if page_cb is not None:
                logging.debug(f'Scheduling transaction page handler for page {page_nr}/{last_page}.')
                asyncio.create_task(page_cb(self._transactions_to_df(page, scope_keys), page_nr, last_page))

        # Fetch transactions from the dataset service.
        transactions = await DatasetsEndpoint(self._client, dataset_id, dss_base) \
            .business_cell(bc_id) \
            .transactions() \
            .index(request_data, page_cb)

        logging.debug(f"Received all transactions at {time.strftime('%X')}, creating data frame...")
        # Map transactions to dicts based on the provided column keys and convert to pandas dataframe.
        df = self._transactions_to_df(transactions, scope_keys)
        logging.debug(f"Data frame created at {time.strftime('%X')}.")

        return df

    def _transactions_to_df(self, transactions, scope_keys):
        """
        Transform the given list of transactions to a DataFrame, using the given `scope_keys` mapping from scope IDs to
        column names.

        :param list[Transaction] transactions: List of transactions as re
        :param dict scope_keys: Dictionary of scope IDs to column keys.
        :return: DataFrame containing the given transactions.
        :rtype: DataFrame
        """
        return DataFrame.from_records([t.to_dict(scope_keys) for t in transactions])

    async def _add_scopes(self, dataset_id, columns, bc_id='all'):
        """
        Find the scope for each provided column and return new list of columns with scope information stored inside.

        :param int dataset_id: Dataset ID to retrieve scopes for.
        :param list[dict] columns: Each column should be a dict with either a `scope_id`, `representation`
            or `name_dataset` property.
        :param str bc_id: (optional) business cell ID.
            (defaults to 'all')
        :return: New list of columns, with for each column an added `scope` property.
        :rtype list[dict]
        """
        all_scopes = await self.get_scopes(dataset_id, bc_id)

        def add_scope(column: dict):
            if ('scope_id' in column) + ('representation' in column) + ('name_dataset' in column) != 1:
                raise ValueError(
                    f'Not exactly one of `scope_id`, `representation` or `name_dataset` provided for column {column}'
                )
            elif 'scope_id' in column:
                scope = all_scopes.find_by_id(column['scope_id'])
            elif 'representation' in column:
                scope = all_scopes.find_by_repr(column['representation'])
            elif 'name_dataset' in column:
                scope = all_scopes.find_by_name_dataset(column['name_dataset'])
            else:
                raise ValueError(f'No scope could be found for column {column}')

            return {**column, 'scope': scope}

        return list(map(add_scope, columns))

    def _add_scope_values(self, dataset_id, column, bc_id='all'):
        """
        Add scope values to the given column.

        :param int dataset_id: Dataset ID to retrieve scope values for.
        :param dict column: Column with `scope` property, for which scope values should be retrieved.
        :param bc_id: (optional) business cell ID.
            (defaults to 'all')
        :return: Copy of the given column with additional `scope_values` property.
        :rtype: dict
        """
        if 'scope' not in column:
            pass

        scope = column['scope']
        scope_values = self.get_scope_values(dataset_id, scope.id, bc_id)

        return {**column, 'scope_values': scope_values}

    def _find_scope_value_filters(self, columns):
        """
        Find all filters that have been defined in the given columns, as a list of scope value IDs.

        :param list[dict] columns: For all columns that have a `filter` and `scope_values` property, the scope values
            to be  filtered will be collected.
        :return: List of all scope value IDs that the columns should be filtered on.
        :rtype: list
        """
        filters = []

        for column in columns:
            filt = dict.get(column, 'filter')
            scope_values = dict.get(column, 'scope_values')

            # If column has no filter or scope_values set, there is nothing to filter.
            if not filt or not scope_values:
                continue

            # Pluck scope value IDs and add to list of all filters.
            filters.extend(scope_values.where_in(filt).pluck('id'))

        return filters

    def _find_aggregation_methods(self, columns):
        """
        Find all aggregation methods that have been defined in the given columns, as a list of dicts where each dict
        contains a `scope_id` and `method`.

        :param list[dict] columns: For all columns that have an `aggregate` and `scope` property, the aggregation
            methods to be applied will be collected.
        :return: List of all aggregation methods that are defined in the columns.
        :rtype list[dict]
        """
        aggregation_methods = []

        for column in columns:
            aggregate = dict.get(column, 'aggregate')
            scope = dict.get(column, 'scope')

            # If column has no aggregate or no scope info set, no aggregation method can be applied for this column.
            if not aggregate or not scope:
                continue

            aggregation_methods.append({
                'scope_id': scope.id,
                'method': aggregate,
            })

        return aggregation_methods

    def _find_scope_keys(self, columns):
        """
        Find the keys that have been defined for all scopes in the given columns. Assumes that all columns have a
        `scope` property set.

        :param list[dict] columns: For all columns, find the scope ID and which key should be used for that scope.
        :return: Dictionary of scope IDs to column keys.
        :rtype: dict
        """
        scope_keys = {}

        for column in columns:
            scope = column['scope']
            key = dict.get(column, 'key', f'scope_{scope.id}')

            scope_keys[scope.id] = key

        return scope_keys
