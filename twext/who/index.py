# -*- test-case-name: twext.who.test.test_xml -*-
##
# Copyright (c) 2006-2014 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

"""
Indexed directory service implementation.
"""

__all__ = [
    "DirectoryService",
    "DirectoryRecord",
]

from twisted.python.constants import Names, NamedConstant
from twisted.internet.defer import succeed, inlineCallbacks, returnValue

from .util import ConstantsContainer, uniqueResult
from .idirectory import FieldName as BaseFieldName
from .expression import MatchExpression, MatchType, MatchFlags
from .directory import (
    DirectoryService as BaseDirectoryService,
    DirectoryRecord as BaseDirectoryRecord,
)



##
# Data type extentions
##

class FieldName(Names):
    memberUIDs = NamedConstant()
    memberUIDs.description = u"member UIDs"
    memberUIDs.multiValue = True



##
# Directory Service
##

class DirectoryService(BaseDirectoryService):
    """
    Generic (and abstract) in-memory-indexed directory service.

    This class implements the record access API in L{BaseDirectoryService} by
    caching all records in an in-memory dictionary.

    Each indexed field has a top-level key in the index and in turn contains
    a dictionary in which keys are field values, and values are directory
    records which have a matching field value for the cooresponding key::

        {
            <FieldName1>: {
                <value1a>: set([<record1a1>, ...]),
                ...
            },
            ...
        }

    Here is an example index for a service with a three user records and one
    group record::

        {
            <FieldName=uid>: {
                u'__calendar-dev__': set([
                    <DirectoryRecord (group)calendar-dev>
                ]),
                u'__dre__': set([
                    <DirectoryRecord (user)dre>
                ]),
                u'__sagen__': set([
                    <DirectoryRecord (user)sagen>
                ]),
                u'__wsanchez__': set([
                    <DirectoryRecord (user)wsanchez>
                ])
            },
            <FieldName=recordType>: {
                <RecordType=group>: set([
                    <DirectoryRecord (group)calendar-dev>,
                ]),
                <RecordType=user>: set([
                    <DirectoryRecord (user)sagen>,
                    <DirectoryRecord (user)wsanchez>
                ])
            },
            <FieldName=shortNames>: {
                u'calendar-dev': set([<DirectoryRecord (group)calendar-dev>]),
                u'dre': set([<DirectoryRecord (user)dre>]),
                u'sagen': set([<DirectoryRecord (user)sagen>]),
                u'wilfredo_sanchez': set([<DirectoryRecord (user)wsanchez>]),
                u'wsanchez': set([<DirectoryRecord (user)wsanchez>])
            },
            <FieldName=emailAddresses>: {
                'dev@bitbucket.calendarserver.org': set([
                    <DirectoryRecord (group)calendar-dev>
                ]),
                'dre@bitbucket.calendarserver.org': set([
                    <DirectoryRecord (user)dre>
                ]),
                'sagen@bitbucket.calendarserver.org': set([
                    <DirectoryRecord (user)sagen>
                ]),
                'shared@example.com': set([
                    <DirectoryRecord (user)sagen>,
                    <DirectoryRecord (user)dre>
                ]),
                'wsanchez@bitbucket.calendarserver.org': set([
                    <DirectoryRecord (user)wsanchez>
                ]),
                'wsanchez@devnull.twistedmatrix.com': set([
                    <DirectoryRecord (user)wsanchez>
                ])
            },
            <FieldName=memberUIDs>: {
                u'__sagen__': set([<DirectoryRecord (group)calendar-dev>]),
                u'__wsanchez__': set([<DirectoryRecord (group)calendar-dev>])
            }
        }

    The field names that are indexed are defined by the C{indexedFields}
    attribute of the service.

    A subclass must override L{loadRecords}, which populates the index.

    @cvar indexedFields: an iterable of field names (C{NamedConstant})
        which are indexed.
    """

    fieldName = ConstantsContainer((
        BaseDirectoryService.fieldName, FieldName
    ))

    indexedFields = (
        BaseFieldName.recordType,
        BaseFieldName.uid,
        BaseFieldName.guid,
        BaseFieldName.shortNames,
        BaseFieldName.emailAddresses,
        FieldName.memberUIDs,
    )


    def __init__(self, realmName):
        BaseDirectoryService.__init__(self, realmName)

        self.flush()


    @property
    def index(self):
        """
        Call L{loadRecords} and return the index.
        """
        self.loadRecords()
        return self._index


    def loadRecords(self):
        """
        Load records.  This method is called by the L{index} property and
        provides a hook into which the index can be updated.

        This method must be implemented by subclasses.

        An example implementation::

            def loadRecords(self):
                self.flush()
                while True:
                    records = readSomeRecordsFromMyBackEnd()
                    if not records:
                        break
                    self.indexRecords(records)
        """
        raise NotImplementedError("Subclasses must implement loadRecords().")


    def indexRecords(self, records):
        """
        Add some records to the index.

        @param records: The records to index.
        @type records: iterable of L{DirectoryRecord}
        """
        index = self._index

        for fieldName in self.indexedFields:
            index.setdefault(fieldName, {})

        for record in records:
            for fieldName in self.indexedFields:
                values = record.fields.get(fieldName, None)

                if values is not None:
                    if not self.fieldName.isMultiValue(fieldName):
                        values = (values,)

                    for value in values:
                        index[fieldName].setdefault(value, set()).add(record)


    def flush(self):
        """
        Flush the index.
        """
        index = {}

        for fieldName in self.indexedFields:
            index.setdefault(fieldName, {})

        self._index = index


    def indexedRecordsFromMatchExpression(self, expression, records=None):
        """
        Finds records in the internal indexes matching a single expression.

        @param expression: An expression.
        @type expression: L{MatchExpression}

        @param records: a set of records to limit the search to. C{None} if
            the whole directory should be searched.
        @type records: L{set} or L{frozenset}

        @return: The matching records.
        @rtype: deferred iterable of L{DirectoryRecord}s
        """
        predicate = MatchFlags.predicator(expression.flags)
        normalize = MatchFlags.normalizer(expression.flags)

        try:
            fieldIndex = self.index[expression.fieldName]
        except KeyError:
            raise TypeError(
                "indexedRecordsFromMatchExpression() was passed an "
                "expression with an unindexed field: {0!r}"
                .format(expression.fieldName)
            )

        matchValue = normalize(expression.fieldValue)
        matchType  = expression.matchType

        if matchType == MatchType.startsWith:
            indexKeys = (
                key for key in fieldIndex
                if predicate(normalize(key).startswith(matchValue))
            )
        elif matchType == MatchType.contains:
            indexKeys = (
                key for key in fieldIndex
                if predicate(matchValue in normalize(key))
            )
        elif matchType == MatchType.equals:
            if predicate(True):
                indexKeys = (matchValue,)
            else:
                indexKeys = (
                    key for key in fieldIndex
                    if normalize(key) != matchValue
                )
        else:
            raise NotImplementedError(
                "Unknown match type: {0!r}".format(matchType)
            )

        matchingRecords = set()
        for key in indexKeys:
            matchingRecords |= fieldIndex.get(key, frozenset())

        # Not necessary, so don't unless we know it's a performance win:
        # if records is not None:
        #     matchingRecords &= records

        return succeed(matchingRecords)


    def unIndexedRecordsFromMatchExpression(self, expression, records=None):
        """
        Finds records not in the internal indexes matching a single expression.

        @param expression: An expression.
        @type expression: L{MatchExpression}

        @param records: a set of records to limit the search to. C{None} if
            the whole directory should be searched.
        @type records: L{set} or L{frozenset}

        @return: The matching records.
        @rtype: deferred iterable of L{DirectoryRecord}s
        """
        predicate = MatchFlags.predicator(expression.flags)
        normalize = MatchFlags.normalizer(expression.flags)

        matchValue = normalize(expression.fieldValue)
        matchType  = expression.matchType

        if matchType == MatchType.startsWith:
            match = lambda fieldValue: predicate(
                fieldValue.startswith(matchValue)
            )
        elif matchType == MatchType.contains:
            match = lambda fieldValue: predicate(matchValue in fieldValue)
        elif matchType == MatchType.equals:
            match = lambda fieldValue: predicate(fieldValue == matchValue)
        else:
            raise NotImplementedError(
                "Unknown match type: {0!r}".format(matchType)
            )

        result = set()

        if records is None:
            records = (
                uniqueResult(values) for values
                in self.index[self.fieldName.uid].itervalues()
            )

        for record in records:
            fieldValues = record.fields.get(expression.fieldName, None)

            if fieldValues is None:
                continue

            for fieldValue in fieldValues:
                if match(normalize(fieldValue)):
                    result.add(record)

        return succeed(result)


    def recordsFromNonCompoundExpression(self, expression, records=None):
        """
        This implementation can handle L{MatchExpression} expressions; other
        expressions are passed up to the superclass.
        """
        if isinstance(expression, MatchExpression):
            if expression.fieldName in self.indexedFields:
                return self.indexedRecordsFromMatchExpression(
                    expression, records=records
                )
            else:
                return self.unIndexedRecordsFromMatchExpression(
                    expression, records=records
                )
        else:
            return BaseDirectoryService.recordsFromNonCompoundExpression(
                self, expression, records=records
            )


    def listRecords(self, recordType):
        records = []
        for record in self.index[self.fieldName.recordType][recordType]:
            records.append(record)
        return succeed(records)



class DirectoryRecord(BaseDirectoryRecord):
    """
    Indexed directory record.
    """

    @inlineCallbacks
    def members(self):
        members = set()
        for uid in getattr(self, "memberUIDs", ()):
            members.add((yield self.service.recordWithUID(uid)))
        returnValue(members)


    def groups(self):
        return self.service.recordsWithFieldValue(
            FieldName.memberUIDs, self.uid
        )
